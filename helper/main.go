// Albion Tools – lokaal hulpprogramma.
//
// Ontvangt de marktdata die de Albion Data Client uploadt (start de client met
// -i http://127.0.0.1:47800, dan gaat er niets naar de openbare database) en
// geeft de beste prijzen en aantallen door aan de Albion Tools-site.
package main

import (
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
	version   = "1.0.0"
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
	Groups     map[string]*group `json:"groups"`
	LastUpload time.Time         `json:"lastUpload"`
	Uploads    int               `json:"uploads"`
	Unknown    map[string]int    `json:"unknownLocations"`
	dirty      bool
}

var st = &store{Groups: map[string]*group{}, Unknown: map[string]int{}}
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
		uploads := st.Uploads
		st.mu.Unlock()
		writeJSON(w, map[string]interface{}{"ok": true, "app": "albion-tools-helper", "version": version,
			"groups": groups, "orders": orders, "uploads": uploads, "lastUpload": last, "unknownLocations": unknown})
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

func main() {
	exe, _ := os.Executable()
	dataFile = filepath.Join(filepath.Dir(exe), "albion-helper-data.json")
	if len(os.Args) > 1 && os.Args[1] == "-data" && len(os.Args) > 2 {
		dataFile = os.Args[2]
	}
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
