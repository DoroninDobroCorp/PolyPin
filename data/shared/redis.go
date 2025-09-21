package shared

import (
	"fmt"
	"sort"
)

// parser/live/<name>
// parser/prematch/<name>
// pairs/live/<name1><name2>
// pairs/prematch/<name1><name2>

const (
	redisDelimiter = "/"

	redisLive = "live"
	redisPrematch = "prematch"

	redisPairs = "pairs"
	redisParser = "parser"
)

func GetRKeyParser(isLive bool, bookmaker string) string {
	live := redisPrematch
	if isLive { live = redisLive }
	
	return fmt.Sprintf("%s%s%s%s%s", redisParser, redisDelimiter, live, redisDelimiter, bookmaker)
}

func GetRKeyPairs(isLive bool, bookmaker1, bookmaker2 string) string {
	live := redisPrematch
	if isLive { live = redisLive }

	names := []string{bookmaker1, bookmaker2}
	sort.Strings(names)

	return fmt.Sprintf("%s%s%s%s%s%s", redisPairs, redisDelimiter, live, redisDelimiter, names[0], names[1])
}

func GetRAllKeysParser(isAll, isLive bool) string {
	if isAll {
		return fmt.Sprintf("%s%s*", redisParser, redisDelimiter)
	}

	live := redisPrematch
	if isLive { live = redisLive }
	return fmt.Sprintf("%s%s%s%s*", redisParser, redisDelimiter, live, redisDelimiter)
}

func GetRAllKeysPairs(isAll, isLive bool) string {
	if isAll {
		return fmt.Sprintf("%s%s*", redisPairs, redisDelimiter)
	}

	live := redisPrematch
	if isLive { live = redisLive }
	return fmt.Sprintf("%s%s%s%s*", redisPairs, redisDelimiter, live, redisDelimiter)
}
