package parse

import (
	"fmt"
	"livebets/parse_serge/internal/entity"
	"livebets/shared"
	"math"
	"strconv"
	"strings"
)

func Football(responseGame *shared.GameData, event entity.EventODDS) *shared.GameData {
	responseGame.SportName = shared.SOCCER
	responseGame.LeagueName = normalizeFootballLeague(responseGame.LeagueName)
	responseGame.HomeName = normalizeFootballTeam(responseGame.HomeName)
	responseGame.AwayName = normalizeFootballTeam(responseGame.AwayName)

	// Add score
	responseGame.HomeScore = event.HomeScore
	responseGame.AwayScore = event.AwayScore

	// Parse periods
	resPeriods := make([]shared.PeriodData, 3)
	for _, period := range event.Periods {
		resPeriod := newPeriod()

		// Check status
		if period.Status != 1 {
			continue
		}

		if period.Number < 0 || period.Number > 2 {
			continue
		}

		// Extract money line
		resPeriod.Win1x2.Win1 = makeOdd(period.MoneyLine.Home)
		resPeriod.Win1x2.WinNone = makeOdd(period.MoneyLine.Draw)
		resPeriod.Win1x2.Win2 = makeOdd(period.MoneyLine.Away)

		// Extract spreads
		for _, spread := range period.Spreads {

			homeLine := floatToLine((event.AwayScore - event.HomeScore) + spread.Hdp)
			awayLine := floatToLine((event.HomeScore - event.AwayScore) - spread.Hdp)

			if _, ok := resPeriod.Handicap[homeLine]; !ok {
				resPeriod.Handicap[homeLine] = &shared.WinHandicap{}
			}
			resPeriod.Handicap[homeLine].Win1 = makeOdd(spread.Home)

			if _, ok := resPeriod.Handicap[awayLine]; !ok {
				resPeriod.Handicap[awayLine] = &shared.WinHandicap{}
			}
			resPeriod.Handicap[awayLine].Win2 = makeOdd(spread.Away)
		}

		// Extract totals
		for _, total := range period.Totals {
			detailBet := floatToLine(total.Points)
			resPeriod.Totals[detailBet] = &shared.WinLessMore{WinMore: makeOdd(total.Over), WinLess: makeOdd(total.Under)}
		}

		// Extract team total
		detailBetHome := floatToLine(period.TeamTotal.Home.Points)
		if detailBetHome != "0.0" {
			resPeriod.FirstTeamTotals[detailBetHome] = &shared.WinLessMore{
				WinMore: makeOdd(period.TeamTotal.Home.Over),
				WinLess: makeOdd(period.TeamTotal.Home.Under),
			}
		}

		detailBetAway := floatToLine(period.TeamTotal.Away.Points)
		if detailBetAway != "0.0" {
			resPeriod.SecondTeamTotals[detailBetAway] = &shared.WinLessMore{
				WinMore: makeOdd(period.TeamTotal.Away.Over),
				WinLess: makeOdd(period.TeamTotal.Away.Under),
			}
		}

		resPeriods[period.Number] = *resPeriod
	}

	responseGame.Periods = resPeriods

	return responseGame
}

func Tennis(tennisData map[int64]*entity.ResponseGame, leagues []entity.LeagueODDS) map[int64]*shared.GameData {
	results := make(map[int64]*shared.GameData)

	for _, league := range leagues {
		for _, event := range league.Events {

			// If not exist match
			if _, ok := tennisData[event.ID]; !ok {
				continue
			}

			children, ok := tennisData[event.ID]
			if !ok {
				continue
			}

			parent, ok := tennisData[children.ParentId]
			if !ok {
				continue
			}

			if _, ok = results[children.ParentId]; !ok {
				results[children.ParentId] = &shared.GameData{
					Pid:        parent.Pid,
					LeagueName: normalizeTennisLeague(parent.LeagueName),
					HomeName:   normalizeTennisTeam(parent.HomeName),
					AwayName:   normalizeTennisTeam(parent.AwayName),
					MatchId:    parent.MatchId,
					HomeScore:  event.HomeScore,
					AwayScore:  event.AwayScore,
					SportName:  shared.TENNIS,
				}
			}
			gameData := results[children.ParentId]

			if gameData.Periods == nil {
				gameData.Periods = make([]shared.PeriodData, 6)
			}

			resPeriods := gameData.Periods

			for _, period := range event.Periods {
				gameNumber := ""

				if period.Number > 80 {
					continue
				}
				if period.Number > 5 {
					gameNumber = fmt.Sprint((period.Number-6)%13 + 1)
					period.Number = (period.Number-6)/13 + 1
				}

				resPeriod := resPeriods[period.Number]

				// Init maps if nil
				if resPeriod.Games == nil {
					resPeriod.Games = make(map[string]*shared.Win1x2Struct)
				}
				if resPeriod.Totals == nil {
					resPeriod.Totals = make(map[string]*shared.WinLessMore)
				}
				if resPeriod.Handicap == nil {
					resPeriod.Handicap = make(map[string]*shared.WinHandicap)
				}
				if resPeriod.FirstTeamTotals == nil {
					resPeriod.FirstTeamTotals = make(map[string]*shared.WinLessMore)
				}
				if resPeriod.SecondTeamTotals == nil {
					resPeriod.SecondTeamTotals = make(map[string]*shared.WinLessMore)
				}

				// Check status
				if period.Status != 1 {
					continue
				}

				// Extract money line
				if period.MoneyLine.Home != 0 {
					if gameNumber != "" {
						resPeriod.Games[gameNumber] = &shared.Win1x2Struct{
							Win1:    makeOdd(period.MoneyLine.Home),
							WinNone: makeOdd(period.MoneyLine.Draw),
							Win2:    makeOdd(period.MoneyLine.Away),
						}
					} else {
						resPeriod.Win1x2.Win1 = makeOdd(period.MoneyLine.Home)
						resPeriod.Win1x2.WinNone = makeOdd(period.MoneyLine.Draw)
						resPeriod.Win1x2.Win2 = makeOdd(period.MoneyLine.Away)
					}
				}

				// Extract totals
				for _, total := range period.Totals {
					if period.Number == 0 && total.Points < 5.0 {
						// total.Points - количество сетов
						// Total в сетах не учитываем
						// Handicap в сетах не учитываем
						goto loopEnd
					}
					detailBet := floatToLine(total.Points)
					resPeriod.Totals[detailBet] = &shared.WinLessMore{WinMore: makeOdd(total.Over), WinLess: makeOdd(total.Under)}
				}

				// Extract team total
				if period.TeamTotal.Home.Points != 0 {
					detailBetHome := floatToLine(period.TeamTotal.Home.Points)
					resPeriod.FirstTeamTotals[detailBetHome] = &shared.WinLessMore{
						WinMore: makeOdd(period.TeamTotal.Home.Over),
						WinLess: makeOdd(period.TeamTotal.Home.Under),
					}
				}

				if period.TeamTotal.Away.Points != 0 {
					detailBetAway := floatToLine(period.TeamTotal.Away.Points)
					resPeriod.SecondTeamTotals[detailBetAway] = &shared.WinLessMore{
						WinMore: makeOdd(period.TeamTotal.Away.Over),
						WinLess: makeOdd(period.TeamTotal.Away.Under),
					}
				}

				// Extract spreads
				for _, spread := range period.Spreads {
					if !strings.Contains(children.HomeName, "Games") {
						break
					}

					homeLine := floatToLine((event.AwayScore - event.HomeScore) + spread.Hdp)
					awayLine := floatToLine((event.HomeScore - event.AwayScore) - spread.Hdp)

					if _, ok := resPeriod.Handicap[homeLine]; !ok {
						resPeriod.Handicap[homeLine] = &shared.WinHandicap{}
					}
					resPeriod.Handicap[homeLine].Win1 = makeOdd(spread.Home)

					if _, ok := resPeriod.Handicap[awayLine]; !ok {
						resPeriod.Handicap[awayLine] = &shared.WinHandicap{}
					}
					resPeriod.Handicap[awayLine].Win2 = makeOdd(spread.Away)
				}

			loopEnd:
				resPeriods[period.Number] = resPeriod
			}

			gameData.Periods = resPeriods
		}
	}

	return results
}

func Basketball(responseGame *shared.GameData, event entity.EventODDS) *shared.GameData {
	responseGame.SportName = shared.BASKETBALL
	responseGame.LeagueName = normalizeBasketballLeague(responseGame.LeagueName)
	responseGame.HomeName = normalizeBasketballTeam(responseGame.HomeName)
	responseGame.AwayName = normalizeBasketballTeam(responseGame.AwayName)

	// Add score
	responseGame.HomeScore = event.HomeScore
	responseGame.AwayScore = event.AwayScore

	// Parse periods
	resPeriods := make([]shared.PeriodData, 6)
	for _, period := range event.Periods {
		resPeriod := newPeriod()

		// Check status
		if period.Status != 1 {
			continue
		}

		if period.Number < 0 || period.Number > 6 {
			continue
		}

		// Extract money line
		resPeriod.Win1x2.Win1 = makeOdd(period.MoneyLine.Home)
		resPeriod.Win1x2.WinNone = makeOdd(period.MoneyLine.Draw)
		resPeriod.Win1x2.Win2 = makeOdd(period.MoneyLine.Away)

		// Extract spreads
		for _, spread := range period.Spreads {

			homeLine := floatToLine((event.AwayScore - event.HomeScore) + spread.Hdp)
			awayLine := floatToLine((event.HomeScore - event.AwayScore) - spread.Hdp)

			if _, ok := resPeriod.Handicap[homeLine]; !ok {
				resPeriod.Handicap[homeLine] = &shared.WinHandicap{}
			}
			resPeriod.Handicap[homeLine].Win1 = makeOdd(spread.Home)

			if _, ok := resPeriod.Handicap[awayLine]; !ok {
				resPeriod.Handicap[awayLine] = &shared.WinHandicap{}
			}
			resPeriod.Handicap[awayLine].Win2 = makeOdd(spread.Away)
		}

		// Extract totals
		for _, total := range period.Totals {
			detailBet := floatToLine(total.Points)
			resPeriod.Totals[detailBet] = &shared.WinLessMore{WinMore: makeOdd(total.Over), WinLess: makeOdd(total.Under)}
		}

		// Extract team total
		detailBetHome := floatToLine(period.TeamTotal.Home.Points)
		if detailBetHome != "0.0" {
			resPeriod.FirstTeamTotals[detailBetHome] = &shared.WinLessMore{
				WinMore: makeOdd(period.TeamTotal.Home.Over),
				WinLess: makeOdd(period.TeamTotal.Home.Under),
			}
		}

		detailBetAway := floatToLine(period.TeamTotal.Away.Points)
		if detailBetAway != "0.0" {
			resPeriod.SecondTeamTotals[detailBetAway] = &shared.WinLessMore{
				WinMore: makeOdd(period.TeamTotal.Away.Over),
				WinLess: makeOdd(period.TeamTotal.Away.Under),
			}
		}

		// period.Number:
		// 0 - match
		// 1 - 1st Half
		// 3 - 1st Quarter
		// 4 - 2nd Quarter
		// 5 - 3rd Quarter
		// 6 - 4th Quarter
		var resPeriodNumber int64
		switch period.Number {
		case 0: // Match
			resPeriodNumber = 0
		case 1: // 1st Half (1)
			// 1st Half (1) => 5
			resPeriodNumber = 5
		case 3, 4, 5, 6: // 1st Quarter (3) ... 4th Quarter (6)
			// 1st Quarter (3) => 1
			resPeriodNumber = period.Number - 2
		default:
			continue
		}
		resPeriods[resPeriodNumber] = *resPeriod
	}

	responseGame.Periods = resPeriods

	return responseGame
}

func Volleyball(responseGame *shared.GameData, event entity.EventODDS) *shared.GameData {
	responseGame.SportName = shared.VOLLEYBALL
	responseGame.LeagueName = normalizeVolleyballLeague(responseGame.LeagueName)
	responseGame.HomeName = normalizeVolleyballTeam(responseGame.HomeName)
	responseGame.AwayName = normalizeVolleyballTeam(responseGame.AwayName)

	// Add score
	responseGame.HomeScore = event.HomeScore
	responseGame.AwayScore = event.AwayScore

	// Parse periods. 0 - match, 1-5 - sets
	resPeriods := make([]shared.PeriodData, 6)
	for _, period := range event.Periods {
		// Check status
		if period.Status != 1 {
			continue
		}

		if period.Number < 0 || period.Number > 5 {
			continue
		}
		
		resPeriod := newPeriod()

		// Extract money line (Win1/Win2)
		resPeriod.Win1x2.Win1 = makeOdd(period.MoneyLine.Home)
		resPeriod.Win1x2.Win2 = makeOdd(period.MoneyLine.Away)

		// Extract spreads (Handicap)
		for _, spread := range period.Spreads {
			// В волейболе гандикап абсолютный, в отличие от футбола/баскетбола, где он зависит от текущего счета
			homeLine := floatToLine(spread.Hdp)
			awayLine := floatToLine(-spread.Hdp)

			if _, ok := resPeriod.Handicap[homeLine]; !ok {
				resPeriod.Handicap[homeLine] = &shared.WinHandicap{}
			}
			resPeriod.Handicap[homeLine].Win1 = makeOdd(spread.Home)

			if _, ok := resPeriod.Handicap[awayLine]; !ok {
				resPeriod.Handicap[awayLine] = &shared.WinHandicap{}
			}
			resPeriod.Handicap[awayLine].Win2 = makeOdd(spread.Away)
		}

		// Extract totals (Over/Under)
		for _, total := range period.Totals {
			detailBet := floatToLine(total.Points)
			resPeriod.Totals[detailBet] = &shared.WinLessMore{WinMore: makeOdd(total.Over), WinLess: makeOdd(total.Under)}
		}

		// Extract team total
		detailBetHome := floatToLine(period.TeamTotal.Home.Points)
		if detailBetHome != "0.0" {
			resPeriod.FirstTeamTotals[detailBetHome] = &shared.WinLessMore{
				WinMore: makeOdd(period.TeamTotal.Home.Over),
				WinLess: makeOdd(period.TeamTotal.Home.Under),
			}
		}

		detailBetAway := floatToLine(period.TeamTotal.Away.Points)
		if detailBetAway != "0.0" {
			resPeriod.SecondTeamTotals[detailBetAway] = &shared.WinLessMore{
				WinMore: makeOdd(period.TeamTotal.Away.Over),
				WinLess: makeOdd(period.TeamTotal.Away.Under),
			}
		}

		resPeriods[period.Number] = *resPeriod
	}

	responseGame.Periods = resPeriods

	return responseGame
}

// TODO: Table Tennis and Handball parse funcs

func makeOdd(value float64) shared.Odd {
	//value = americanToDecimal(value)
	return shared.Odd{Value: value}
}

// AmericanToDecimal converts American odds to decimal format
func americanToDecimal(odds float64) float64 {
	if odds == 0 {
		return 0
	}

	var result float64
	if odds > 0 {
		result = math.Floor(((odds/100)+1)*1000) / 1000
	} else {
		result = math.Floor(((100/math.Abs(odds))+1)*1000) / 1000
	}
	return result
}

func floatToLine(value float64) string {
	line := strconv.FormatFloat(value, 'f', 2, 64)
	return strings.TrimSuffix(line, "0")
}

func newPeriod() *shared.PeriodData {
	return &shared.PeriodData{
		Totals:           make(map[string]*shared.WinLessMore),
		Handicap:         make(map[string]*shared.WinHandicap),
		FirstTeamTotals:  make(map[string]*shared.WinLessMore),
		SecondTeamTotals: make(map[string]*shared.WinLessMore),
	}
}
