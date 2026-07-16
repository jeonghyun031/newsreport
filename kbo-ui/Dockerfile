FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 3000

# Docker Compose 내에서 백엔드와 별도로 실행되므로, uvicorn을 호출하는 dev 스크립트 대신 start 스크립트를 실행합니다.
CMD ["npm", "start"]
