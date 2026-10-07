from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def hello():
    return {"message": "Vercel Python works"}