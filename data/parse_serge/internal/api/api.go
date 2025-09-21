package api

import (
	"compress/gzip"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"livebets/parse_serge/cmd/config"
	"livebets/parse_serge/internal/entity"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	ODDS_FORMAT      = "Decimal"
	SINCE            = "0" // важный параметр, чтобы цены передавались актуальные
	preMatchTimeDiff = 48 * time.Hour
)

type API struct {
	cfg    config.APIConfig
	client *http.Client
}

func New(cfg config.APIConfig) *API {
	transport := &http.Transport{}

	if cfg.Proxy != "" {
		proxyURL, err := url.Parse(cfg.Proxy)
		if err != nil {
			fmt.Printf("[ERROR] Неверный URL прокси: %v\n", err)
		} else {
			transport.Proxy = http.ProxyURL(proxyURL)
		}
	}

	client := &http.Client{
		Transport: transport,
		Timeout:   time.Second * time.Duration(cfg.Timeout),
	}

	return &API{
		cfg:    cfg,
		client: client,
	}
}

func (api *API) GetEvents(sportID entity.SportId, isLive string) ([]*entity.Event, error) {
	req, err := http.NewRequest(http.MethodGet, api.cfg.Url+api.cfg.EventsUrl, nil)
	if err != nil {
		return nil, err
	}

	query := req.URL.Query()
	query.Add("sportId", fmt.Sprintf("%d", sportID))
	query.Add("isLive", isLive)
	req.URL.RawQuery = query.Encode()

	if len(api.cfg.Username) != 0 {
		req.SetBasicAuth(api.cfg.Username, api.cfg.Password)
	}
	req.Header.Set("Accept", "*/*")
	// req.Header.Set("Content-Type", "application/json")
	// req.Header.Set("Accept-Encoding", "gzip, deflate, br, zstd")
	req.Header.Set("Accept-Encoding", "gzip")
	req.Header.Set("token", api.cfg.Token)

	resp, err := api.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, errors.New(resp.Status)
	}

	encodedBody, err := gzip.NewReader(resp.Body)
	if err != nil {
		return nil, err
	}
	defer encodedBody.Close()

	body, err := io.ReadAll(encodedBody)
	if err != nil {
		return nil, err
	}

	if len(body) == 0 {
		return nil, nil
	}

	body = normalizeBodyJSON(body)

	var result entity.ResponseMatchData
	if err = json.Unmarshal(body, &result); err != nil {
		return nil, err
	}

	timeNow := time.Now()

	events := make([]*entity.Event, 0, 1024)
	for _, league := range result.League {
		for _, event := range league.Events {
			if isLive == "1" { // Live
				// Only Live = true
				if event.LiveStatus != 1 {
					continue
				}

				if sportID == entity.FootballID {
					// Skip corners matches
					coners := "(Corners)"
					if strings.Contains(event.Home, coners) ||
						strings.Contains(event.Away, coners) {
						continue
					}
				}

			} else { // PreMatch
				diff := -timeNow.Sub(event.Starts) // -(minus) before timeNow.Sub() is to diff will be > 0
				if diff < 0 || diff > preMatchTimeDiff {
					continue
				}
			}

			event.League = league.Name
			events = append(events, &event)
		}
	}

	return events, nil
}

func (api *API) GetOdds(sportID entity.SportId, isLive string) (*entity.ResponseODDSData, error) {
	req, err := http.NewRequest(http.MethodGet, api.cfg.Url+api.cfg.OddsUrl, nil)
	if err != nil {
		return nil, err
	}

	query := req.URL.Query()
	query.Add("sportId", fmt.Sprintf("%d", sportID))
	query.Add("isLive", isLive)
	req.URL.RawQuery = query.Encode()

	if len(api.cfg.Username) != 0 {
		req.SetBasicAuth(api.cfg.Username, api.cfg.Password)
	}
	req.Header.Set("Accept", "*/*")
	// req.Header.Set("Content-Type", "application/json")
	// req.Header.Set("Accept-Encoding", "gzip, deflate, br, zstd")
	req.Header.Set("Accept-Encoding", "gzip")
	req.Header.Set("token", api.cfg.Token)

	resp, err := api.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, errors.New(resp.Status)
	}

	encodedBody, err := gzip.NewReader(resp.Body)
	if err != nil {
		return nil, err
	}
	defer encodedBody.Close()

	body, err := io.ReadAll(encodedBody)
	if err != nil {
		return nil, err
	}

	if len(body) == 0 {
		return nil, nil
	}

	body = normalizeBodyJSON(body)

	var result entity.ResponseODDSData
	if err = json.Unmarshal(body, &result); err != nil {
		return nil, err
	}

	return &result, nil
}

func normalizeBodyJSON(body []byte) []byte {
	bodyString := string(body)

	bodyString = strings.Replace(bodyString, "\\\"", "\"", -1)
	bodyString = strings.TrimPrefix(bodyString, "\"")
	bodyString = strings.TrimSuffix(bodyString, "\"")

	return []byte(bodyString)
}
