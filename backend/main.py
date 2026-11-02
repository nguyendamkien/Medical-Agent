from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import auth, chat, conversations, facilities, profile   # conversations tạo ở bước 8

settings = get_settings()

app = FastAPI(
    title="ChatBot Y Tế API",
    description="API backend cho chatbot hỏi đáp y tế, sử dụng Google Gemini.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(profile.router)
app.include_router(facilities.router)

@app.get("/")
def health_check() -> dict[str, str]:
    return {"status": "ok", "message": "ChatBot Y Tế API đang hoạt động"}
