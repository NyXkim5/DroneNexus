FROM nginx:1.27-alpine

# Install openssl for dev cert generation
RUN apk add --no-cache openssl curl

# Copy nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Remove default nginx config that conflicts with ours
RUN rm -f /etc/nginx/conf.d/default.conf.bak

# Generate self-signed cert at build time for dev use
# Production deployments should mount real certs via volume at /etc/nginx/certs
RUN mkdir -p /etc/nginx/certs && \
    openssl req -x509 -nodes -days 365 \
    -newkey rsa:2048 \
    -keyout /etc/nginx/certs/server.key \
    -out /etc/nginx/certs/server.crt \
    -subj "/C=US/ST=California/L=Irvine/O=OVERWATCH/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:overwatch,IP:127.0.0.1"

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -kf https://localhost:443/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
