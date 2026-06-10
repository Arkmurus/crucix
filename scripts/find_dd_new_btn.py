"""Find the New DD button handler in dd-reports.html."""
with open('public/dd-reports.html', encoding='utf-8') as f:
    content = f.read()

marker = "document.getElementById('dd-new-btn')"
idx = content.find(marker)
if idx >= 0:
    # Find the enclosing function
    start = content.rfind("addEventListener", 0, idx)
    end = content.find("});", idx) + 3
    print(f"Position {start} to {end}")
    print(content[start:end])
