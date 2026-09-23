from fastapi import FastAPI
app=FastAPI(title="本草溯源")
@app.get("/health")
def health(): return {"status":"ok"}
