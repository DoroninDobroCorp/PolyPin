package shared

import "time"

type GameData struct {
	// Get from matches
	Pid        int64  `json:"Pid"`
	LeagueName string `json:"LeagueName"`
	HomeName   string `json:"homeName"`
	AwayName   string `json:"awayName"`
	MatchId    string `json:"MatchId"`
	IsLive     bool   `json:"isLive"`

	// Get from odds
	HomeScore float64      `json:"HomeScore"`
	AwayScore float64      `json:"AwayScore"`
	Periods   []PeriodData `json:"Periods"`

	// Get from config
	Source    Parser    `json:"Source"`
	SportName SportName `json:"SportName"`
	CreatedAt time.Time `json:"CreatedAt"`

	Raw interface{} `json:"Raw"`
}

type PeriodData struct {
	Win1x2           Win1x2Struct             `json:"Win1x2"`
	Games            map[string]*Win1x2Struct `json:"Games"`
	Totals           map[string]*WinLessMore  `json:"Totals"`
	Handicap         map[string]*WinHandicap  `json:"Handicap"`
	FirstTeamTotals  map[string]*WinLessMore  `json:"FirstTeamTotals"`
	SecondTeamTotals map[string]*WinLessMore  `json:"SecondTeamTotals"`
}

type WinHandicap struct {
	Win1 Odd `json:"Win1"`
	Win2 Odd `json:"Win2"`
}

type WinLessMore struct {
	WinMore Odd `json:"WinMore"`
	WinLess Odd `json:"WinLess"`
}

type Win1x2Struct struct {
	Win1    Odd `json:"Win1"`
	WinNone Odd `json:"WinNone"`
	Win2    Odd `json:"Win2"`
}

type Odd struct {
	Value float64     `json:"value"`
	Raw   interface{} `json:"raw"`
}
