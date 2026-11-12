FROM postgres:16-alpine
COPY --from=minio/mc:latest /usr/bin/mc /usr/bin/mc
COPY backup.sh restore-test.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore-test.sh
ENTRYPOINT ["/usr/local/bin/backup.sh"]
