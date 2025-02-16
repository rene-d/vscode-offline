featured:
    python3 vscode.py --version 1.96.4 -c files.in --prune

latest:
    python3 vscode.py -c files.in --prune

clean:
    rm -f query_*.json response_*.json