// Albion Tools – lokaal hulpprogramma.
//
// Ontvangt de marktdata die de Albion Data Client uploadt (start de client met
// -i http://127.0.0.1:47800, dan gaat er niets naar de openbare database) en
// geeft de beste prijzen en aantallen door aan de Albion Tools-site.
package main

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	version   = "1.2.0"
	listen    = "127.0.0.1:47800"
	mergeGap  = 15 * time.Second // binnen deze tijd hoort een nieuwe upload bij dezelfde lijst (volgende pagina)
	keepFor   = 24 * time.Hour
	priceUnit = 10000 // de game stuurt prijzen x 10.000
)

// Marktplaatsen (cluster-ID's uit de game-data). Een ID met "-Auction2" is de Black Market.
var locations = map[string]string{
	"0007": "Thetford", "1002": "Lymhurst", "2004": "Bridgewatch", "3008": "Martlock",
	"4002": "Fort Sterling", "3005": "Caerleon", "5003": "Brecilien", "3003": "Black Market",
}

//go:embed items_index.txt
var itemsIndexTxt string
var itemByAlbionID = map[int32]string{}

func init() {
	for _, line := range strings.Split(itemsIndexTxt, "\n") {
		if i := strings.IndexByte(line, ' '); i > 0 {
			if n, err := strconv.Atoi(line[:i]); err == nil {
				itemByAlbionID[int32(n)] = strings.TrimSpace(line[i+1:])
			}
		}
	}
}

// Verkoopgeschiedenis zoals de client die stuurt als je in-game de prijsgrafiek van een item opent.
type histPoint struct {
	ItemAmount   int64  `json:"ItemAmount"`
	SilverAmount uint64 `json:"SilverAmount"`
	Timestamp    uint64 `json:"Timestamp"` // .NET-ticks
}
type histUpload struct {
	AlbionID  int32        `json:"AlbionId"`
	Location  string       `json:"LocationId"`
	Quality   int          `json:"QualityLevel"`
	Timescale int          `json:"Timescale"` // 0 = 24 uur, 1 = 7 dagen, 2 = 4 weken
	Histories []*histPoint `json:"MarketHistories"`
}
type histStat struct {
	Item     string    `json:"item_id"`
	City     string    `json:"city"`
	Quality  int       `json:"quality"`
	PerDay   float64   `json:"per_day"`
	Days     int       `json:"days"`
	AvgPrice float64   `json:"avg_price"`
	Scale    int       `json:"timescale"`
	Seen     time.Time `json:"seen"`
}

const ticksAtUnixEpoch = 621355968000000000

func ingestHistory(body []byte) (string, error) {
	var up histUpload
	if err := json.Unmarshal(body, &up); err != nil {
		return "", err
	}
	item := itemByAlbionID[up.AlbionID]
	city := cityOf(up.Location)
	if item == "" || city == "" || len(up.Histories) == 0 {
		st.mu.Lock()
		if city == "" && up.Location != "" {
			st.Unknown[up.Location]++
		}
		st.mu.Unlock()
		return "", nil
	}
	q := up.Quality
	if q < 1 {
		q = 1
	}
	now := time.Now().UTC()
	today := now.Truncate(24 * time.Hour)
	var items int64
	var silver float64
	days := map[int64]bool{}
	for _, h := range up.Histories {
		if h == nil || h.ItemAmount <= 0 || h.Timestamp < ticksAtUnixEpoch {
			continue
		}
		t := time.UnixMilli(int64((h.Timestamp - ticksAtUnixEpoch) / 10000)).UTC()
		if up.Timescale == 0 {
			if now.Sub(t) > 24*time.Hour { // 24-uursgrafiek: alles van de laatste 24 uur
				continue
			}
		} else if !t.Before(today) || t.Before(today.Add(-7*24*time.Hour)) { // 7 volle dagen, vandaag telt niet mee
			continue
		}
		items += h.ItemAmount
		silver += float64(h.SilverAmount)
		days[t.Truncate(24*time.Hour).Unix()] = true
	}
	stat := &histStat{Item: item, City: city, Quality: q, Scale: up.Timescale, Seen: time.Now()}
	if up.Timescale == 0 {
		stat.PerDay, stat.Days = float64(items), 1
	} else {
		stat.PerDay, stat.Days = float64(items)/7, len(days)
	}
	if items > 0 {
		stat.AvgPrice = silver / float64(items) / priceUnit
	}
	key := item + "|" + strconv.Itoa(q) + "|" + city
	st.mu.Lock()
	// een 7-dagen- of 4-wekenbeeld gaat voor een 24-uursbeeld
	if old := st.Hist[key]; old == nil || up.Timescale != 0 || old.Scale == 0 || now.Sub(old.Seen) > time.Hour {
		st.Hist[key] = stat
	}
	st.dirty = true
	st.LastUpload = time.Now()
	st.mu.Unlock()
	return item + " in " + city, nil
}

var resourceRe = regexp.MustCompile(`^T\d_(ORE|WOOD|FIBER|HIDE|ROCK|METALBAR|PLANKS|CLOTH|LEATHER|STONEBLOCK)$`)

type order struct {
	ID          int64  `json:"Id"`
	ItemTypeID  string `json:"ItemTypeId"`
	LocationID  string `json:"LocationId"`
	Quality     int    `json:"QualityLevel"`
	Enchantment int    `json:"EnchantmentLevel"`
	Price       int64  `json:"UnitPriceSilver"`
	Amount      int    `json:"Amount"`
	AuctionType string `json:"AuctionType"`
	Expires     string `json:"Expires"`
}

type group struct {
	Item    string           `json:"item"`
	City    string           `json:"city"`
	Quality int              `json:"quality"`
	Type    string           `json:"type"` // offer = sell order, request = buy order
	Seen    time.Time        `json:"seen"`
	Orders  map[int64]*order `json:"orders"`
}

type store struct {
	mu         sync.Mutex
	Groups     map[string]*group    `json:"groups"`
	LastUpload time.Time            `json:"lastUpload"`
	Uploads    int                  `json:"uploads"`
	Unknown    map[string]int       `json:"unknownLocations"`
	Hist       map[string]*histStat `json:"history"`
	dirty      bool
}

var st = &store{Groups: map[string]*group{}, Unknown: map[string]int{}, Hist: map[string]*histStat{}}
var dataFile string

func cityOf(loc string) string {
	if c, ok := locations[loc]; ok {
		return c
	}
	if strings.HasSuffix(loc, "-Auction2") {
		return "Black Market"
	}
	return ""
}

func itemID(o *order) string {
	id := strings.TrimSpace(o.ItemTypeID)
	if strings.Contains(id, "@") || o.Enchantment <= 0 {
		return id
	}
	if resourceRe.MatchString(id) {
		return fmt.Sprintf("%s_LEVEL%d@%d", id, o.Enchantment, o.Enchantment)
	}
	return fmt.Sprintf("%s@%d", id, o.Enchantment)
}

func expired(o *order, now time.Time) bool {
	if o.Expires == "" {
		return false
	}
	t, err := time.Parse(time.RFC3339Nano, o.Expires)
	if err != nil {
		t, err = time.Parse("2006-01-02T15:04:05.999999", o.Expires)
	}
	return err == nil && t.Before(now)
}

func ingestOrders(body []byte) (int, error) {
	var up struct {
		Orders []*order `json:"Orders"`
	}
	if err := json.Unmarshal(body, &up); err != nil {
		return 0, err
	}
	now := time.Now()
	st.mu.Lock()
	defer st.mu.Unlock()
	batch := map[string][]*order{}
	n := 0
	for _, o := range up.Orders {
		if o == nil || o.Price <= 0 {
			continue
		}
		city := cityOf(o.LocationID)
		if city == "" {
			st.Unknown[o.LocationID]++
			continue
		}
		q := o.Quality
		if q < 1 {
			q = 1
		}
		t := strings.ToLower(o.AuctionType)
		if t != "offer" && t != "request" {
			continue
		}
		key := itemID(o) + "|" + strconv.Itoa(q) + "|" + city + "|" + t
		batch[key] = append(batch[key], o)
		n++
	}
	for key, list := range batch {
		p := strings.Split(key, "|")
		q, _ := strconv.Atoi(p[1])
		g := st.Groups[key]
		if g == nil || now.Sub(g.Seen) > mergeGap {
			// nieuwe momentopname van deze lijst: oude orders vervallen
			g = &group{Item: p[0], Quality: q, City: p[2], Type: p[3], Orders: map[int64]*order{}}
			st.Groups[key] = g
		}
		for _, o := range list {
			g.Orders[o.ID] = o
		}
		g.Seen = now
	}
	if n > 0 {
		st.LastUpload = now
		st.Uploads++
		st.dirty = true
	}
	return n, nil
}

type record struct {
	ItemID     string `json:"item_id"`
	City       string `json:"city"`
	Quality    int    `json:"quality"`
	SellMin    int64  `json:"sell_price_min"`
	SellDate   string `json:"sell_price_min_date"`
	SellAmount int    `json:"sell_amount"`
	SellTotal  int    `json:"sell_amount_total"`
	BuyMax     int64  `json:"buy_price_max"`
	BuyDate    string `json:"buy_price_max_date"`
	BuyAmount  int    `json:"buy_amount"`
	BuyTotal   int    `json:"buy_amount_total"`
	Source     string `json:"source"`
}

func aggregate(since time.Time) []*record {
	now := time.Now()
	st.mu.Lock()
	defer st.mu.Unlock()
	recs := map[string]*record{}
	for key, g := range st.Groups {
		if now.Sub(g.Seen) > keepFor {
			delete(st.Groups, key)
			continue
		}
		if g.Seen.Before(since) {
			continue
		}
		var best int64
		amt, total := 0, 0
		for _, o := range g.Orders {
			if expired(o, now) {
				continue
			}
			price := o.Price / priceUnit
			if price <= 0 {
				continue
			}
			total += o.Amount
			better := best == 0 || (g.Type == "offer" && price < best) || (g.Type == "request" && price > best)
			if better {
				best, amt = price, o.Amount
			} else if price == best {
				amt += o.Amount
			}
		}
		if best == 0 {
			continue
		}
		rk := g.Item + "|" + strconv.Itoa(g.Quality) + "|" + g.City
		r := recs[rk]
		if r == nil {
			r = &record{ItemID: g.Item, City: g.City, Quality: g.Quality, Source: "local",
				SellDate: "0001-01-01T00:00:00", BuyDate: "0001-01-01T00:00:00"}
			recs[rk] = r
		}
		date := g.Seen.UTC().Format("2006-01-02T15:04:05")
		if g.Type == "offer" {
			r.SellMin, r.SellDate, r.SellAmount, r.SellTotal = best, date, amt, total
		} else {
			r.BuyMax, r.BuyDate, r.BuyAmount, r.BuyTotal = best, date, amt, total
		}
	}
	out := make([]*record, 0, len(recs))
	for _, r := range recs {
		out = append(out, r)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ItemID+out[i].City < out[j].ItemID+out[j].City })
	return out
}

func cors(w http.ResponseWriter) {
	h := w.Header()
	h.Set("Access-Control-Allow-Origin", "*")
	h.Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	h.Set("Access-Control-Allow-Headers", "*")
	h.Set("Access-Control-Allow-Private-Network", "true")
}

func writeJSON(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

func handler(w http.ResponseWriter, r *http.Request) {
	cors(w)
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/")
	switch {
	case r.Method == http.MethodPost:
		body, _ := io.ReadAll(io.LimitReader(r.Body, 32<<20))
		if strings.HasSuffix(path, "marketorders.ingest") {
			n, err := ingestOrders(body)
			if err != nil {
				log.Printf("Kon orders niet lezen: %v", err)
				http.Error(w, "bad json", http.StatusBadRequest)
				return
			}
			if n > 0 {
				log.Printf("Ontvangen: %d orders", n)
			}
		} else if strings.HasSuffix(path, "markethistories.ingest") {
			what, err := ingestHistory(body)
			if err != nil {
				log.Printf("Kon geschiedenis niet lezen: %v", err)
			} else if what != "" {
				log.Printf("Ontvangen: verkoopgeschiedenis van %s", what)
			}
		}
		w.WriteHeader(http.StatusOK) // andere uploads (history, goud) worden genegeerd
	case path == "/status" || path == "":
		st.mu.Lock()
		groups, orders := len(st.Groups), 0
		for _, g := range st.Groups {
			orders += len(g.Orders)
		}
		last := ""
		if !st.LastUpload.IsZero() {
			last = st.LastUpload.UTC().Format(time.RFC3339)
		}
		unknown := map[string]int{}
		for k, v := range st.Unknown {
			unknown[k] = v
		}
		uploads, hist := st.Uploads, len(st.Hist)
		st.mu.Unlock()
		writeJSON(w, map[string]interface{}{"ok": true, "app": "albion-tools-helper", "version": version,
			"groups": groups, "orders": orders, "histories": hist, "uploads": uploads, "lastUpload": last, "unknownLocations": unknown})
	case path == "/location":
		curLoc.Lock()
		out := map[string]interface{}{"id": curLoc.ID, "log": clientLog}
		if !curLoc.T.IsZero() {
			out["t"] = curLoc.T.UnixMilli()
		}
		curLoc.Unlock()
		_, err := os.Stat(clientLog)
		out["logFound"] = err == nil
		writeJSON(w, out)
	case path == "/history":
		since := time.Time{}
		if s := r.URL.Query().Get("since"); s != "" {
			if ms, err := strconv.ParseInt(s, 10, 64); err == nil {
				since = time.UnixMilli(ms)
			}
		}
		st.mu.Lock()
		out := []*histStat{}
		for key, h := range st.Hist {
			if time.Since(h.Seen) > keepFor {
				delete(st.Hist, key)
				continue
			}
			if !h.Seen.Before(since) {
				out = append(out, h)
			}
		}
		st.mu.Unlock()
		writeJSON(w, out)
	case path == "/prices":
		since := time.Time{}
		if s := r.URL.Query().Get("since"); s != "" {
			if ms, err := strconv.ParseInt(s, 10, 64); err == nil {
				since = time.UnixMilli(ms)
			}
		}
		writeJSON(w, aggregate(since))
	default:
		http.NotFound(w, r)
	}
}

func load() {
	b, err := os.ReadFile(dataFile)
	if err != nil {
		return
	}
	st.mu.Lock()
	defer st.mu.Unlock()
	if json.Unmarshal(b, st) != nil || st.Groups == nil {
		st.Groups = map[string]*group{}
	}
	if st.Unknown == nil {
		st.Unknown = map[string]int{}
	}
	if st.Hist == nil {
		st.Hist = map[string]*histStat{}
	}
}

func save() {
	st.mu.Lock()
	if !st.dirty {
		st.mu.Unlock()
		return
	}
	b, err := json.Marshal(st)
	st.dirty = false
	st.mu.Unlock()
	if err == nil {
		tmp := dataFile + ".tmp"
		if os.WriteFile(tmp, b, 0o644) == nil {
			os.Rename(tmp, dataFile)
		}
	}
}

// ---------- huidige zone (uit het logbestand van de Albion Data Client) ----------
// De client schrijft "Updating player location to 4206." in albiondata-client.log
// (in de map waaruit hij gestart is). We lezen alleen dat bestand; niets aan het spel.
var (
	locRe  = regexp.MustCompile(`Updating player location to ([A-Za-z0-9@_-]+)`)
	curLoc = struct {
		sync.Mutex
		ID, File string
		T        time.Time
	}{}
	clientLog string
)

func watchClientLog() {
	var off int64
	var lastFile string
	var lastSize int64
	for range time.Tick(time.Second) {
		f := clientLog
		fi, err := os.Stat(f)
		if err != nil {
			continue
		}
		if f != lastFile || fi.Size() < lastSize {
			off = 0 // nieuw of opnieuw begonnen logbestand
		}
		lastFile, lastSize = f, fi.Size()
		if fi.Size() <= off {
			continue
		}
		fh, err := os.Open(f)
		if err != nil {
			continue
		}
		if off == 0 && fi.Size() > 4<<20 {
			off = fi.Size() - 4<<20
		}
		fh.Seek(off, io.SeekStart)
		b, _ := io.ReadAll(io.LimitReader(fh, 8<<20))
		fh.Close()
		// alleen hele regels verwerken
		end := strings.LastIndexByte(string(b), '\n')
		if end < 0 {
			continue
		}
		off += int64(end + 1)
		for _, line := range strings.Split(string(b[:end]), "\n") {
			if m := locRe.FindStringSubmatch(strings.TrimRight(line, "\r")); m != nil {
				curLoc.Lock()
				if curLoc.ID != m[1] {
					log.Printf("Zone: %s", m[1])
				}
				curLoc.ID, curLoc.T, curLoc.File = m[1], time.Now(), f
				curLoc.Unlock()
			}
		}
	}
}

func main() {
	exe, _ := os.Executable()
	dataFile = filepath.Join(filepath.Dir(exe), "albion-helper-data.json")
	clientLog = filepath.Join(filepath.Dir(exe), "albiondata-client.log")
	for i := 1; i+1 < len(os.Args); i++ {
		switch os.Args[i] {
		case "-data":
			dataFile = os.Args[i+1]
		case "-clientlog":
			clientLog = os.Args[i+1]
		}
	}
	go watchClientLog()
	load()
	go func() {
		for range time.Tick(20 * time.Second) {
			save()
		}
	}()
	fmt.Println("Albion Tools-hulpprogramma " + version)
	fmt.Println("Luistert op http://" + listen + "  (laat dit venster open)")
	fmt.Println("Start de Albion Data Client met:  -i http://" + listen)
	fmt.Println("Open daarna de markt of Black Market in Albion; je ziet hier hoeveel orders binnenkomen.")
	srv := &http.Server{Addr: listen, Handler: http.HandlerFunc(handler), ReadHeaderTimeout: 10 * time.Second}
	if err := srv.ListenAndServe(); err != nil {
		fmt.Println("Kon niet starten:", err)
		fmt.Println("Draait het hulpprogramma misschien al? Druk op Enter om te sluiten.")
		fmt.Scanln()
	}
}
