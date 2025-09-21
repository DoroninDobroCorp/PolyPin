include .env
export $(shell sed 's/=.*//' .env)

NAME=parse_serge

build_pp:  ### run parse_serge in local environment
	@echo "Building ${NAME} binary..."
	cd ../${NAME} && env GOOS=linux CGO_ENABLED=0 go build -o ${NAME} ./cmd
	@echo "Done!"
	./${NAME}
.PHONY: ./${NAME}