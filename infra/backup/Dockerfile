FROM alpine:3.20
RUN apk add --no-cache postgresql16-client aws-cli openssl tzdata
COPY run.sh /run.sh
RUN chmod 0555 /run.sh
USER 65534:65534
ENTRYPOINT ["/run.sh"]
