package entity

import (
	"livebets/shared"
	"time"
)

type ResponseGame struct {
	// Get from matches
	Pid        int64  `json:"Pid"`
	LeagueName string `json:"LeagueName"`
	HomeName   string `json:"homeName"`
	AwayName   string `json:"awayName"`
	MatchId    string `json:"MatchId"`
	ParentId   int64  `json:"ParentId"`
	IsLive     bool   `json:"isLive"`

	// Get from odds
	HomeScore float64             `json:"HomeScore"`
	AwayScore float64             `json:"AwayScore"`
	Periods   []shared.PeriodData `json:"Periods"`

	// Get from config
	Source    shared.Parser    `json:"Source"`
	SportName shared.SportName `json:"SportName"`
	CreatedAt time.Time        `json:"CreatedAt"`
}
