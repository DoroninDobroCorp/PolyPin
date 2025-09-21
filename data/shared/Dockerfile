FROM golang:1.23-alpine AS golang

# Build argument
ARG code_dir

WORKDIR /app

# Europe/Moscow time zone
RUN apk add --no-cache tzdata \
   && cp /usr/share/zoneinfo/Europe/Moscow /etc/localtime


# Download Go modules
COPY ./shared /usr/local/go/src/livebets/shared
RUN cd /usr/local/go/src/livebets/shared && go mod download

# Download Go modules
COPY ./$code_dir/go.mod .
COPY ./$code_dir/go.sum .
RUN go mod download

# Copy the source code
COPY ./$code_dir .

# Build
RUN GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -ldflags="-s -w" -trimpath -o $code_dir ./cmd

# Second build phase
FROM scratch

ARG code_dir

# Copy local time: Europe/Moscow
COPY --from=golang /etc/localtime /etc/localtime

# Copy configuration file: ./configs/common.yml
COPY --from=golang /app/configs /configs

# Copy root sertificates for HTTPS connection
COPY --from=golang /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/

# Copy compiled program binary code
COPY --from=golang /app/$code_dir /$code_dir

# ! ! !
# "/app" must be redefined in docker-compose.yaml
#
#services:
#  my_service:
#    entrypoint: /my_service
ENTRYPOINT ["/app"]
