import sys
import re

def get_modal():
    with open('E:\\R2P_OLAP_-Online-Analytical-Processing-\\UI\\description_page.html', 'r', encoding='utf-8') as f:
        content = f.read()
    
    start_str = '<div class="modal-overlay" id="processing-modal"'
    start = content.find(start_str)
    
    end_str = '<!-- Right Panel (~65%) -->'
    end_idx = content.find('</div>\n    </div>\n\n    <div class="modal-overlay" id="intro-modal">')
    
    return content[start:end_idx + 14]

with open('E:\\R2P_OLAP_-Online-Analytical-Processing-\\scratch\\modal_out.txt', 'w', encoding='utf-8') as f:
    f.write(get_modal())
