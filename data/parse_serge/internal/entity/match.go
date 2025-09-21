package entity

import "time"

type ResponseMatchData struct {
	SportID int64    `json:"sportId"`
	Last    int64    `json:"last"`
	League  []League `json:"league"`
}

type League struct {
	ID     int64   `json:"id"`
	Name   string  `json:"name"`
	Events []Event `json:"events"`
}

type Event struct {
	ID                int64     `json:"id"`
	Starts            time.Time `json:"starts"`
	League            string
	Home              string `json:"home"`
	Away              string `json:"away"`
	RotNum            string `json:"rotNum"`
	LiveStatus        int64  `json:"liveStatus"`
	Status            string `json:"status"`
	ResultingUnit     string `json:"resultingUnit"`
	ParlayRestriction int64  `json:"parlayRestriction"`
	ParentId          int64  `json:"parentId"`
	AltTeaser         bool   `json:"altTeaser"`
}
