package main

import (
	"context"
	"livebets/parse_serge/cmd/config"
	"livebets/parse_serge/internal/api"
	"livebets/parse_serge/internal/sender"
	"livebets/parse_serge/internal/service"
	"livebets/shared"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"

	"github.com/rs/zerolog"
)

func HealthCheckHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
}

// Основная функция
func main() {
	ctx, cancelFunc := context.WithCancel(context.Background())

	// Init config
	logger := zerolog.New(os.Stderr).With().Timestamp().Logger()
	logger.Info().Msg(">> Starting Parse_serge")
	appConfig, err := config.ProvideAppMPConfig()
	if err != nil {
		logger.Fatal().Err(err).Msg("failed to load app configuration")
	}

	sendChan := make(chan shared.GameData, 150)
	defer close(sendChan)

	api := api.New(appConfig.APIConfig)
	sender := sender.New(appConfig.SenderConfig, sendChan)
	service := service.New(api, sendChan, &logger)

	wg := &sync.WaitGroup{}

	wg.Add(1)
	go sender.SendingToAnalyzer(ctx, wg)

	if appConfig.APIConfig.ParseLive {
		logger.Info().Msg("Start parse: Live")
	} else {
		logger.Info().Msg("Start parse: PreMatch")
	}

	wg.Add(1)
	go service.Run(ctx, appConfig.APIConfig, wg)

	http.HandleFunc("/health", HealthCheckHandler)
	http.HandleFunc("/output", sender.HandleClientConn)

	server := &http.Server{Addr: ":" + appConfig.Port}

	go func() {
		if err = server.ListenAndServe(); err != http.ErrServerClosed {
			logger.Fatal().Err(err).Msg("failed to start server")
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, os.Interrupt, syscall.SIGTERM, syscall.SIGINT)
	<-quit

	cancelFunc()
	wg.Wait()

	if err = server.Shutdown(context.Background()); err != nil {
		logger.Fatal().Err(err).Msg("failed to stop server")
	}

	logger.Info().Msg(">> Stopping parse_serge")
}
