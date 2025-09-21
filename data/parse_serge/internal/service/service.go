package service

import (
	"context"
	"fmt"
	"livebets/parse_serge/cmd/config"
	"livebets/parse_serge/internal/api"
	"livebets/parse_serge/internal/entity"
	"livebets/parse_serge/internal/parse"
	"livebets/shared"
	"strings"
	"sync"
	"time"

	"github.com/rs/zerolog"
)

type Service struct {
	api            *api.API
	sendChan       chan<- shared.GameData
	tennisData     map[int64]*entity.ResponseGame
	sportData      map[int64]*shared.GameData
	sportDataMutex sync.RWMutex
	logger         *zerolog.Logger
}

func New(
	api *api.API,
	sendChan chan<- shared.GameData,
	logger *zerolog.Logger,
) *Service {
	sportData := make(map[int64]*shared.GameData, 4096)
	tennisData := make(map[int64]*entity.ResponseGame)
	return &Service{
		api:        api,
		sendChan:   sendChan,
		sportData:  sportData,
		tennisData: tennisData,
		logger:     logger,
	}
}

const (
	LIVE_MODE     = "1"
	PREMATCH_MODE = "0"
)

func (s *Service) Run(ctx context.Context, cfg config.APIConfig, wg *sync.WaitGroup) {
	defer wg.Done()

	if cfg.SportConfig.Football {
		wg.Add(1)
		go s.runSport(ctx, cfg, entity.FootballID, wg)
	}
	if cfg.SportConfig.Tennis {
		wg.Add(1)
		go s.runTennis(ctx, cfg, entity.TennisID, wg)
	}
	if cfg.SportConfig.Basketball {
		wg.Add(1)
		go s.runSport(ctx, cfg, entity.BasketballID, wg)
	}
	if cfg.SportConfig.Volleyball {
		wg.Add(1)
		go s.runSport(ctx, cfg, entity.VolleyballID, wg)
	}
}

func (s *Service) runSport(ctx context.Context, cfg config.APIConfig, sportID entity.SportId, wg *sync.WaitGroup) {
	defer wg.Done()

	eventsInterval := time.Duration(cfg.Live.EventsInterval) * time.Second
	oddsInterval := time.Duration(cfg.Live.OddsInterval) * time.Second
	isLive := LIVE_MODE
	if !cfg.ParseLive {
		eventsInterval = time.Duration(cfg.Prematch.EventsInterval) * time.Second
		oddsInterval = time.Duration(cfg.Prematch.OddsInterval) * time.Second
		isLive = PREMATCH_MODE
	}

	eventsTicker := time.NewTicker(eventsInterval)
	defer eventsTicker.Stop()

	oddsTicker := time.NewTicker(oddsInterval)
	defer oddsTicker.Stop()

	for {
		select {
		case <-eventsTicker.C:
			start := time.Now()

			events, err := s.api.GetEvents(sportID, isLive)
			if err != nil {
				s.logger.Error().Err(err).Msgf("[Service.Run] error get events. sportID - %d", sportID)
				continue
			}

			elapsed := time.Since(start)
			s.logger.Info().Msgf("SportID: %2d. Время получения данных для %d матчей: %s", sportID, len(events), elapsed)

			s.sportDataMutex.Lock()
			for _, event := range events {
				s.sportData[event.ID] = &shared.GameData{
					Pid:        event.ID,
					LeagueName: event.League,
					HomeName:   event.Home,
					AwayName:   event.Away,
					MatchId:    fmt.Sprintf("%d", event.ID),
				}
			}
			s.sportDataMutex.Unlock()

		case <-oddsTicker.C:
			oddsData, err := s.api.GetOdds(sportID, isLive)
			if err != nil {
				s.logger.Error().Err(err).Msgf("[Service.Run] error get odds. sportID - %d", sportID)
				continue
			}

			if oddsData == nil {
				continue
			}

			eventCounter := 0
			for _, league := range oddsData.Leagues {
				for _, event := range league.Events {

					s.sportDataMutex.RLock()
					responseGame, ok := s.sportData[event.ID]
					s.sportDataMutex.RUnlock()

					// If not exist match
					if !ok {
						continue
					}

					switch sportID {
					case entity.FootballID:
						responseGame = parse.Football(responseGame, event)
					case entity.BasketballID:
						responseGame = parse.Basketball(responseGame, event)
					case entity.VolleyballID:
						responseGame = parse.Volleyball(responseGame, event)
					case entity.HandballID:
						// responseGame = parse.Handball(responseGame, event)
					case entity.TableTennisID:
						// responseGame = parse.TableTennis(responseGame, event)
					}

					responseGame.IsLive = cfg.ParseLive
					responseGame.CreatedAt = time.Now().Add(-oddsData.Time)

					s.sendChan <- *responseGame

					eventCounter++
				}
			}

			s.logger.Info().Msgf("SportID: %2d. В анализатор отправлено %d матчей.", sportID, eventCounter)

		case <-ctx.Done():
			return
		}
	}
}

func (s *Service) runTennis(ctx context.Context, cfg config.APIConfig, sportID entity.SportId, wg *sync.WaitGroup) {
	defer wg.Done()

	eventsInterval := time.Duration(cfg.Live.EventsInterval) * time.Second
	oddsInterval := time.Duration(cfg.Live.OddsInterval) * time.Second
	isLive := LIVE_MODE
	if !cfg.ParseLive {
		eventsInterval = time.Duration(cfg.Prematch.EventsInterval) * time.Second
		oddsInterval = time.Duration(cfg.Prematch.OddsInterval) * time.Second
		isLive = PREMATCH_MODE
	}

	eventsTicker := time.NewTicker(eventsInterval)
	defer eventsTicker.Stop()

	oddsTicker := time.NewTicker(oddsInterval)
	defer oddsTicker.Stop()

	for {
		select {
		case <-eventsTicker.C:
			start := time.Now()

			events, err := s.api.GetEvents(sportID, isLive)
			if err != nil {
				s.logger.Error().Err(err).Msgf("[Service.Run] error get events. sportID - %d", sportID)
				continue
			}

			elapsed := time.Since(start)
			s.logger.Info().Msgf("SportID: %2d. Время получения данных для %d матчей: %s", sportID, len(events), elapsed)

			for _, event := range events {

				s.tennisData[event.ID] = &entity.ResponseGame{
					Pid:        event.ID,
					LeagueName: event.League,
					HomeName:   event.Home,
					AwayName:   event.Away,
					MatchId:    fmt.Sprintf("%d", event.ID),
					ParentId:   event.ParentId,
				}

				if event.ParentId == 0 {
					// This is parent event
					s.tennisData[event.ID].ParentId = event.ID
					continue
				}

				// Add parent event and normalize team names
				if _, ok := s.tennisData[event.ParentId]; !ok {
					s.tennisData[event.ParentId] = &entity.ResponseGame{
						Pid:        event.ParentId,
						LeagueName: event.League,
						HomeName:   strings.Split(event.Home, " (")[0], // Remove (Games) and (Sets)
						AwayName:   strings.Split(event.Away, " (")[0], // Remove (Games) and (Sets)
						MatchId:    fmt.Sprintf("%d", event.ParentId),
						ParentId:   0,
					}
				}
			}

		case <-oddsTicker.C:
			oddsData, err := s.api.GetOdds(sportID, isLive)
			if err != nil {
				s.logger.Error().Err(err).Msgf("[Service.Run] error get odds. sportID - %d", sportID)
				continue
			}

			if oddsData == nil {
				continue
			}

			results := parse.Tennis(s.tennisData, oddsData.Leagues)

			eventCounter := 0
			for _, responseGame := range results {
				responseGame.IsLive = cfg.ParseLive
				responseGame.CreatedAt = time.Now().Add(-oddsData.Time)

				s.sendChan <- *responseGame

				eventCounter++
			}

			s.logger.Info().Msgf("SportID: %2d. В анализатор отправлено %d матчей.", sportID, eventCounter)

		case <-ctx.Done():
			return
		}
	}
}
