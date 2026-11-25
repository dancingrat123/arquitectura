FROM nginx:alpine

RUN apk add --no-cache openssl

COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /usr/local/bin/sirif-entrypoint.sh

RUN chmod +x /usr/local/bin/sirif-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/sirif-entrypoint.sh"]
