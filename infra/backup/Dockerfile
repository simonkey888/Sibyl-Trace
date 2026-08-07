FROM alpine:3.20.10@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
RUN apk add --no-cache postgresql16-client aws-cli openssl python3 tzdata
COPY run.sh /run.sh
RUN chmod 0555 /run.sh
USER 65534:65534
ENTRYPOINT ["/run.sh"]
