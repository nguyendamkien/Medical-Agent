# Frontend ChatBot Y Tế — xem docs/DOCKER.md
# Giai đoạn 1: build React bằng Vite
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Rỗng = gọi API cùng địa chỉ với trang (nginx chuyển /api sang backend). Vite gắn cứng lúc build.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# Giai đoạn 2: nginx phục vụ file tĩnh + chuyển tiếp /api
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
