package config

import (
	"livebets/parse_serge/utils"
	"reflect"
	"strings"
	"sync"

	"github.com/mitchellh/mapstructure"
	"github.com/spf13/viper"
)

var (
	once         sync.Once
	cachedConfig AppConfig
)

type AppConfig struct {
	APIConfig    `mapstructure:"pin"`
	SenderConfig `mapstructure:"sender"`
	Port         string `mapstructure:"port"`
}

type APIConfig struct {
	Url         string       `mapstructure:"url"`
	EventsUrl   string       `mapstructure:"events_url"`
	OddsUrl     string       `mapstructure:"odds_url"`
	Username    string       `mapstructure:"username"`
	Password    string       `mapstructure:"password"`
	Token       string       `mapstructure:"token"`
	Proxy       string       `mapstructure:"proxy"`
	Timeout     int          `mapstructure:"timeout"`
	Live        StreamConfig `mapstructure:"live"`
	Prematch    StreamConfig `mapstructure:"prematch"`
	SportConfig SportConfig  `mapstructure:"sport"`
	ParseLive   bool         `mapstructure:"parse_live"`
}

type StreamConfig struct {
	EventsInterval int `mapstructure:"events_interval"`
	OddsInterval   int `mapstructure:"odds_interval"`
}

type SportConfig struct {
	Football   bool `mapstructure:"football"`
	Tennis     bool `mapstructure:"tennis"`
	Basketball bool `mapstructure:"basketball"`
	Volleyball bool `mapstructure:"volleyball"`
	TableTennis bool `mapstructure:"table_tennis"`
	Handball bool `mapstructure:"handball"`
}

type SenderConfig struct {
	Url string `mapstructure:"url"`
}

func ProvideAppMPConfig() (AppConfig, error) {
	var err error
	once.Do(func() {
		viper.AutomaticEnv()
		viper.SetEnvKeyReplacer(strings.NewReplacer(".", "_"))

		viper.AddConfigPath("configs")
		viper.SetConfigName("common")
		viper.SetConfigType("yml")
		err = viper.ReadInConfig()
		if err != nil {
			return
		}

		BindEnvs(cachedConfig)

		hooks := viper.DecodeHook(mapstructure.ComposeDecodeHookFunc(utils.DefaultDecodeHooks()...))
		err = viper.Unmarshal(&cachedConfig, hooks)
		if err != nil {
			return
		}
	})

	return cachedConfig, err
}

func BindEnvs(iface interface{}, parts ...string) {
	ifv := reflect.ValueOf(iface)
	ift := reflect.TypeOf(iface)
	for i := 0; i < ift.NumField(); i++ {
		v := ifv.Field(i)
		t := ift.Field(i)
		tv, ok := t.Tag.Lookup("mapstructure")
		if !ok {
			continue
		}
		switch v.Kind() {
		case reflect.Struct:
			BindEnvs(v.Interface(), append(parts, tv)...)
		default:
			viper.BindEnv(strings.Join(append(parts, tv), "."))
		}
	}
}
