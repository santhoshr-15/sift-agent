import fitz

def create_trap_test_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "This is a trap test PDF.")
    doc.save(path)
    doc.close()
