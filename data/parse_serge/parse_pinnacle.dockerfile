# syntax=docker/dockerfile:1
FROM golang:1.23

# Set destination for COPY
WORKDIR /app

# Download Go modules
COPY ./shared /usr/local/go/src/livebets/shared
RUN cd /usr/local/go/src/livebets/shared && go mod download

# Download Go modules
COPY ./parse_serge/go.mod .
COPY ./parse_serge/go.sum .
RUN go mod download

# Copy the source code. Note the slash at the end, as explained in
# https://docs.docker.com/reference/dockerfile/#copy
COPY ./parse_serge .

# Build
RUN env GOOS=linux CGO_ENABLED=0 go build -o parse_serge ./cmd
# Optional:
# To bind to a TCP port, runtime parameters must be supplied to the docker command.
# But we can document in the Dockerfile what ports
# the application is going to listen on by default.
# https://docs.docker.com/reference/dockerfile/#expose

# Run
CMD ["./parse_serge"]