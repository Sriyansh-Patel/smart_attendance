import uvicorn
from app.config import HOST, PORT

if __name__ == "__main__":
    print(f"Starting Smart Attendance Server on http://{HOST}:{PORT}")
    print(f"Swagger API Docs: http://localhost:{PORT}/docs")
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
