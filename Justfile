all: app ext

app:
    python3 vscode-app.py -d latest

ext:
    python3 vscode-ext.py -d latest -c files.in

clean:
    rm -f query_*.json response_*.json