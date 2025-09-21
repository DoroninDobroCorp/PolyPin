package shared

var (
	extraPercents = []struct {
		Min, Max, ExtraPercent float64
	}{
		{2.29, 2.75, 1.03},
		{2.75, 3.2, 1.04},
		{3.2, 3.7, 1.05},
	}
)

// Получение дополнительного процента
func getExtraPercent(pinnacleOdd float64) float64 {
	for _, ep := range extraPercents {
		if pinnacleOdd >= ep.Min && pinnacleOdd < ep.Max {
			return ep.ExtraPercent
		}
	}
	return 1.0
}

// Расчет ROI
func CalculateROI(sansaOdd, pinnacleOdd float64, margin float64, marketType int, secondBookmakerName Parser, sportName SportName) float64 {
	extraPercent := getExtraPercent(pinnacleOdd)

	switch secondBookmakerName {
	case LOBBET:
		if sportName == TENNIS {
			return (sansaOdd/(pinnacleOdd*margin*extraPercent) - 1 - 0.03) * 100 * 0.67
		}
		if marketType == 0 {
			return (sansaOdd/(pinnacleOdd*margin*extraPercent) - 1 - 0.03) * 100 * 0.67
		}
		if marketType < 0 {
			return (sansaOdd/(pinnacleOdd*margin*extraPercent) - 1 - 0.015) * 100 * 0.75
		}
		return (sansaOdd/(pinnacleOdd*margin*extraPercent) - 1 - 0.03) * 100 * 0.67
	case LADBROKES:
		if sportName == TENNIS {
			return (sansaOdd/(pinnacleOdd*margin*extraPercent) - 1 - 0.02) * 100 * 0.75
		}
		if marketType == 0 {
			return (sansaOdd/(pinnacleOdd*margin*extraPercent) - 1 - 0.02) * 100 * 0.75
		}
		if marketType < 0 {
			return (sansaOdd/(pinnacleOdd*margin*extraPercent) - 1) * 100 * 0.85
		}
		return (sansaOdd/(pinnacleOdd*margin*extraPercent) - 1 - 0.02) * 100 * 0.75

	}

	return (sansaOdd/(pinnacleOdd*margin*extraPercent) - 1 - 0.03) * 100 * 0.67
}
