package entity

import "time"

type ResponseODDSData struct {
	Time    time.Duration
	SportID int64        `json:"sportId"`
	Last    int64        `json:"last"`
	Leagues []LeagueODDS `json:"leagues"`
}

type LeagueODDS struct {
	ID     int64       `json:"id"`
	Events []EventODDS `json:"events"`
}

type EventODDS struct {
	ID           int64        `json:"id"`
	AwayScore    float64      `json:"awayScore"`
	HomeScore    float64      `json:"homeScore"`
	AwayRedCards int          `json:"awayRedCards"`
	HomeRedCards int          `json:"homeRedCards"`
	Periods      []PeriodODDS `json:"periods"`
}

type PeriodODDS struct {
	LineID             int64        `json:"lineId"`
	Number             int64        `json:"number"`
	Cutoff             time.Time    `json:"cutoff"`
	MaxSpread          float64      `json:"maxSpread"`
	MaxMoneyline       float64      `json:"maxMoneyline"`
	MaxTeamTotal       float64      `json:"maxTeamTotal"`
	MoneylineUpdatedAt time.Time    `json:"moneylineUpdatedAt"`
	TeamTotalUpdatedAt time.Time    `json:"teamTotalUpdatedAt"`
	MaxTotal           float64      `json:"maxTotal"`
	Status             int64        `json:"status"`
	SpreadUpdateAt     time.Time    `json:"spreadUpdateAt"`
	TotalUpdateAt      time.Time    `json:"totalUpdateAt"`
	Spreads            []SpreadODDS `json:"spreads"`
	Totals             []TotalODDS  `json:"totals"`
	HomeScore          float64      `json:"homeScore"`
	AwayScore          float64      `json:"awayScore"`
	AwayRedCards       int          `json:"awayRedCards"`
	HomeRedCards       int          `json:"homeRedCards"`
	TeamTotal          TeamTotal    `json:"teamTotal"`
	MoneyLine          MoneyLine    `json:"moneyLine"`
}

type MoneyLine struct {
	Home float64 `json:"home"`
	Away float64 `json:"away"`
	Draw float64 `json:"draw"`
}

type TeamTotal struct {
	Home Home `json:"home"`
	Away Away `json:"away"`
}

type Home struct {
	Points float64 `json:"points"`
	Over   float64 `json:"over"`
	Under  float64 `json:"under"`
}

type Away struct {
	Points float64 `json:"points"`
	Over   float64 `json:"over"`
	Under  float64 `json:"under"`
}

type SpreadODDS struct {
	AltLineID int64   `json:"altLineId"`
	Hdp       float64 `json:"hdp"`
	Home      float64 `json:"home"`
	Away      float64 `json:"away"`
	Max       float64 `json:"max"`
}

type TotalODDS struct {
	AltLineID int64   `json:"altLineId"`
	Points    float64 `json:"points"`
	Over      float64 `json:"over"`
	Under     float64 `json:"under"`
	Max       float64 `json:"max"`
}
