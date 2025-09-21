package parse

import (
	"encoding/json"
	"fmt"
	"github.com/stretchr/testify/assert"
	"livebets/parse_serge/internal/entity"
	"livebets/shared"
	"os"
	"path"
	"strings"
	"testing"
)

const (
	jsonPath = "test_json"
)

func getFile(fileName string) ([]byte, error) {
	fullFileName := path.Join(jsonPath, fileName)
	return os.ReadFile(fullFileName)
}

func GetBasketballMatch(t *testing.T, matchFileName string) *shared.GameData {
	results := getBasketballData(t, matchFileName)
	if len(results) == 0 {
		t.Fatal("[ERROR] Не удалось найти теннисный матч.")
	}
	return results[0]
}

func GetTennisMatch(t *testing.T, matchFileName string) *shared.GameData {
	results := getTennisData(t, matchFileName)
	if len(results) == 0 {
		t.Fatal("[ERROR] Не удалось найти теннисный матч.")
	}
	for _, result := range results {
		return result
	}
	return nil
}

func getBasketballData(t *testing.T, matchFileName string) []*shared.GameData {
	events, _ := getEvents(t, matchFileName+"_events.json")

	data := make(map[int64]*shared.GameData)

	for _, event := range events {
		data[event.ID] = &shared.GameData{
			Pid:        event.ID,
			LeagueName: event.League,
			HomeName:   event.Home,
			AwayName:   event.Away,
			MatchId:    fmt.Sprintf("%d", event.ID),
		}
	}

	oddsData, _ := getOdds(t, matchFileName+"_odds.json")

	results := make([]*shared.GameData, 0)

	for _, league := range oddsData.Leagues {
		for _, event := range league.Events {

			responseGame, ok := data[event.ID]
			// If not exist match
			if !ok {
				continue
			}

			responseGame = Basketball(responseGame, event)
			responseGame.IsLive = true

			results = append(results, responseGame)
		}
	}

	return results
}

func getTennisData(t *testing.T, matchFileName string) map[int64]*shared.GameData {
	events, _ := getEvents(t, matchFileName+"_events.json")

	tennisData := make(map[int64]*entity.ResponseGame)

	for _, event := range events {

		tennisData[event.ID] = &entity.ResponseGame{
			Pid:        event.ID,
			LeagueName: event.League,
			HomeName:   event.Home,
			AwayName:   event.Away,
			MatchId:    fmt.Sprintf("%d", event.ID),
			ParentId:   event.ParentId,
		}

		if event.ParentId == 0 {
			// This is parent event
			tennisData[event.ID].ParentId = event.ID
			continue
		}

		// Add parent event and normalize team names
		if _, ok := tennisData[event.ParentId]; !ok {
			tennisData[event.ParentId] = &entity.ResponseGame{
				Pid:        event.ParentId,
				LeagueName: event.League,
				HomeName:   strings.Split(event.Home, " (")[0], // Remove (Games) and (Sets)
				AwayName:   strings.Split(event.Away, " (")[0], // Remove (Games) and (Sets)
				MatchId:    fmt.Sprintf("%d", event.ParentId),
				ParentId:   0,
			}
		}
	}

	oddsData, _ := getOdds(t, matchFileName+"_odds.json")

	results := Tennis(tennisData, oddsData.Leagues)

	return results
}

func getEvents(t *testing.T, fileName string) ([]*entity.Event, error) {

	body, err := getFile(fileName)
	if err != nil {
		t.Fatalf("[ERROR] Не удалось прочитать файл %s. Err: %s", fileName, err)
	}

	var result entity.ResponseMatchData
	if err = json.Unmarshal(body, &result); err != nil {
		t.Fatalf("[ERROR] Не удалось Unmarshal файл %s. Err: %s", fileName, err)
	}

	events := make([]*entity.Event, 0, 1535)

	// const preMatchTimeDiff = 48 * time.Hour
	//timeNow := time.Now()

	for _, league := range result.League {
		for _, event := range league.Events {
			//diff := -timeNow.Sub(event.Starts) // -(minus) before timeNow.Sub() is to diff will be > 0
			//if diff < 0 || diff > preMatchTimeDiff {
			//	continue
			//}

			event.League = league.Name
			events = append(events, &event)
		}
	}

	return events, nil
}

func getOdds(t *testing.T, fileName string) (*entity.ResponseODDSData, error) {

	body, err := getFile(fileName)
	if err != nil {
		t.Fatalf("[ERROR] Не удалось прочитать файл %s. Err: %s", fileName, err)
	}

	var result entity.ResponseODDSData
	if err = json.Unmarshal(body, &result); err != nil {
		t.Fatalf("[ERROR] Не удалось Unmarshal файл %s. Err: %s", fileName, err)
	}

	return &result, nil
}

func CheckTotals(t *testing.T, expectedTotals, totals map[string]*shared.WinLessMore, caption string) {
	assert.Equal(t, len(expectedTotals), len(totals), "len("+caption+")")

	for key, total := range totals {
		expected, ok := expectedTotals[key]
		if !ok {
			expected = &shared.WinLessMore{}
		}
		assert.Equal(t, expected.WinLess, total.WinLess, caption+".WinLess <"+key)
		assert.Equal(t, expected.WinMore, total.WinMore, caption+".WinMore >"+key)
	}
}

func CheckHandicap(t *testing.T, expectedHandicap, matchHandicap map[string]*shared.WinHandicap, caption string) {
	assert.Equal(t, len(expectedHandicap), len(matchHandicap), "len("+caption+")")

	for key, handicap := range matchHandicap {
		expected, ok := expectedHandicap[key]
		if !ok {
			expected = &shared.WinHandicap{}
		}
		assert.Equal(t, expected.Win1, handicap.Win1, caption+".Win1 "+key)
		assert.Equal(t, expected.Win2, handicap.Win2, caption+".Win2 "+key)
	}
}

func CheckGamesWin1x2(t *testing.T, expectedWin1x2, gamesWin1x2 map[string]*shared.Win1x2Struct, caption string) {
	assert.Equal(t, len(expectedWin1x2), len(gamesWin1x2), "len("+caption+")")

	for key, win1x2 := range gamesWin1x2 {
		expected, ok := expectedWin1x2[key]
		if !ok {
			expected = &shared.Win1x2Struct{}
		}
		assert.Equal(t, expected.Win1, win1x2.Win1, caption+key+".Win1")
		assert.Equal(t, expected.Win2, win1x2.Win2, caption+key+".Win2")
	}
}
