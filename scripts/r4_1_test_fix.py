from pathlib import Path

p = Path("services/backend/tests/test_paper_v5_r4.py")
text = p.read_text()
old = 'with pytest.raises(Exception, match="did not match requested condition"):'
new = 'with pytest.raises(Exception, match="conditionId mismatch"):'
if old in text:
    text = text.replace(old, new)
p.write_text(text)
