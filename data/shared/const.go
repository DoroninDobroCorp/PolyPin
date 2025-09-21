package shared

type Parser string

const (
	PINNACLE     Parser = "Pinnacle"
	LOBBET       Parser = "Lobbet"
	LADBROKES    Parser = "Ladbrokes"
	BETCENTER    Parser = "Betcenter"
	SANSABET     Parser = "Sansabet"
	STARCASINO   Parser = "StarCasino"
	UNIBET       Parser = "Unibet"
	FONBET       Parser = "Fonbet"
	SBBET        Parser = "Sbbet"
	MAXBET       Parser = "Maxbet"
	PINNACLE_OUR Parser = "Pinnacle_Our"
	SERGE        Parser = "Serge"
	PINNACLE888  Parser = "Pinnacle888"
)

type SportName string

const (
	SOCCER      SportName = "Soccer"
	TENNIS      SportName = "Tennis"
	BASKETBALL  SportName = "Basketball"
	VOLLEYBALL  SportName = "Volleyball"
	HANDBALL    SportName = "Handball"
	TABLETENNIS SportName = "TableTennis"
)
