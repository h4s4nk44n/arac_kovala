from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return "Hello, World!"

if __name__ == '__main__':
    print("--- Starting Minimal Test Server on http://127.0.0.1:5000 ---")
    # Using default settings for maximum compatibility
    app.run(port=8080, debug=True)