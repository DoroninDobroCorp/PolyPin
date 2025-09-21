package parse

import (
	"regexp"
	"strings"
)

func normalizeFootballLeague(league string) string {
	return normalizeAllName(league)
}

func normalizeTennisLeague(league string) string {
	split := strings.Split(league, "-")
	return normalizeAllName(split[0])
}

func normalizeBasketballLeague(league string) string {
	return normalizeAllName(league)
}

func normalizeFootballTeam(team string) string {
	re := regexp.MustCompile(`\b(FC|SC|FK|CF|CD|NK|LK|U\d+)\b`) // отдельные слова: FC,SC,FK,CF,CD,NK,LK или U затем цифры
	team = re.ReplaceAllString(team, "")
	return normalizeAllName(team)
}

func normalizeTennisTeam(team string) string {
	return normalizeAllName(team)
}

func normalizeBasketballTeam(team string) string {
	re := regexp.MustCompile(`\b(BC|BK|BBC|CD)\b`) // отдельные слова: BC, BK, BBC, CD
	team = re.ReplaceAllString(team, "")
	return normalizeAllName(team)
}

func normalizeVolleyballLeague(league string) string {
	return normalizeAllName(league)
}

func normalizeVolleyballTeam(team string) string {
	re := regexp.MustCompile(`\b(VC)\b`) // отдельные слова: VC
	team = re.ReplaceAllString(team, "")
	return normalizeAllName(team)
}

// TODO: Table Tennis and Handball normalize funcs

func normalizeAllName(name string) string {
	// Удаляем запятые и дефисы
	name = strings.ReplaceAll(name, ",", "")
	name = strings.ReplaceAll(name, "-", "")

	// Удаляем двойные пробелы и пробелы в начале и конце
	name = strings.Join(strings.Fields(name), " ")

	// Переводим строку в нижний регистр
	name = strings.ToLower(name)

	return name
}
