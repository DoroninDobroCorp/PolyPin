package sender

import (
	"context"
	"encoding/json"
	"github.com/gorilla/websocket"
	"livebets/parse_serge/cmd/config"
	"livebets/shared"
	"log"
	"net/http"
	"sync"
	"time"
)

type Sender struct {
	cfg            config.SenderConfig
	analyzerConn   *websocket.Conn
	clientConns    map[*websocket.Conn]bool
	clientConnsMux sync.Mutex
	sendChan       <-chan shared.GameData
	upgrader       websocket.Upgrader
}

func New(
	cfg config.SenderConfig,
	sendChan <-chan shared.GameData,
) *Sender {
	analyzerConn := connectToAnalyzer(cfg)

	upgrader := websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool {
			return true
		},
	}

	return &Sender{
		cfg:          cfg,
		analyzerConn: analyzerConn,
		clientConns:  make(map[*websocket.Conn]bool),
		sendChan:     sendChan,
		upgrader:     upgrader,
	}
}

// Функция подключения к анализатору
func connectToAnalyzer(cfg config.SenderConfig) *websocket.Conn {
	var analyzerConnection *websocket.Conn
	var err error
	for {
		analyzerConnection, _, err = websocket.DefaultDialer.Dial(cfg.Url, nil)
		if err != nil {
			log.Printf("[ERROR] Ошибка подключения к анализатору: %v", err)
			time.Sleep(5 * time.Second)
			continue
		}
		break
	}
	return analyzerConnection
}

func (s *Sender) SendingToAnalyzer(ctx context.Context, wg *sync.WaitGroup) error {
	defer wg.Done()

	for {
		select {
		case gameData := <-s.sendChan:
			gameData.Source = shared.PINNACLE

			byteMsg, err := json.MarshalIndent(gameData, "", "  ")
			if err != nil {
				return err
			}

			if err := s.analyzerConn.WriteMessage(websocket.TextMessage, byteMsg); err != nil {
				log.Printf("[ERROR] Ошибка отправки данных клиенту (%v): %v", s.analyzerConn.RemoteAddr(), err)
				return err
			}

			s.sendingToClients(byteMsg)

		case <-ctx.Done():
			s.clientConnsMux.Lock()
			for conn := range s.clientConns {
				conn.Close()
				delete(s.clientConns, conn)
			}
			s.clientConnsMux.Unlock()
			return nil
		}
	}
}

func (s *Sender) HandleClientConn(w http.ResponseWriter, r *http.Request) {
	conn, err := s.upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[ERROR] Ошибка при обновлении соединения до WebSocket: %v", err)
		return
	}

	s.clientConnsMux.Lock()
	s.clientConns[conn] = true
	s.clientConnsMux.Unlock()

	log.Printf("[INFO] Новый клиент подключен: %s", conn.RemoteAddr())

	go func() {
		defer func() {
			s.clientConnsMux.Lock()
			delete(s.clientConns, conn)
			s.clientConnsMux.Unlock()
			conn.Close()
			log.Printf("[INFO] Клиент отключен: %s", conn.RemoteAddr())
		}()

		for {
			_, _, err := conn.ReadMessage()
			if err != nil {
				if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
					log.Printf("[ERROR] Ошибка чтения от клиента: %v", err)
				}
				return
			}
		}
	}()
}

func (s *Sender) sendingToClients(byteMsg []byte) {
	s.clientConnsMux.Lock()
	defer s.clientConnsMux.Unlock()

	for conn := range s.clientConns {
		if err := conn.WriteMessage(websocket.TextMessage, byteMsg); err != nil {
			log.Printf("[ERROR] Ошибка отправки данных клиенту (%v): %v", conn.RemoteAddr(), err)
			conn.Close()
			delete(s.clientConns, conn)
		}
	}
}
