# frontend/Dockerfile — Multi-stage build for EpiNet React frontend
# Stage 1: Build React SPA bundle with Vite
# WHY node:20-alpine: official lightweight Node.js 20 build image
FROM node:20-alpine AS builder
WORKDIR /app

# Copy dependency manifests first to leverage Docker layer caching
COPY package.json package-lock.json ./
RUN npm ci

# Copy source code and build production bundle
COPY . .
# Set VITE_API_URL to empty string so fetch calls use relative /api paths handled by nginx
ENV VITE_API_URL=""
RUN npm run build

# Stage 2: Serve production bundle with unprivileged Nginx
# WHY nginxinc/nginx-unprivileged:alpine: hardened official unprivileged Nginx on Alpine Linux;
# runs natively as user 'nginx' (uid 101) without requiring root permissions
FROM nginxinc/nginx-unprivileged:alpine
WORKDIR /usr/share/nginx/html

# Copy custom Nginx proxy configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy compiled static assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
