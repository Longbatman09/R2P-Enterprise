import sys

with open('scratch/modal_out.txt', 'r', encoding='utf-8') as f:
    modal_html = f.read()

with open('scratch/modal_css.txt', 'r', encoding='utf-8') as f:
    modal_css = f.read()

with open('E:\\R2P_OLAP_-Online-Analytical-Processing-\\UI\\exam_detail_page.html', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('</body>', f'\n{modal_html}\n</body>')

if '</style>' in content:
    content = content.replace('</style>', f'\n{modal_css}\n</style>')
else:
    content = content.replace('</head>', f'\n<style>\n{modal_css}\n</style>\n</head>')

start_poll = content.find('let isPolling = true;')
end_poll = content.find('setTimeout(pollStatus, 1500);', start_poll)
end_poll = content.find('setTimeout(pollStatus, 1500);', end_poll + 1) + 29

new_poll = '''
                document.getElementById('add-data-modal').style.display = 'none';
                
                const modal = document.getElementById('processing-modal');
                modal.classList.add('active');
                document.getElementById('modal-spinner').style.display = 'block';
                document.getElementById('modal-title').textContent = 'ANALYZING...';
                document.getElementById('modal-status').textContent = 'Processing documents...';
                document.getElementById('modal-status').style.display = 'block';
                document.getElementById('modal-progress-container').style.display = 'block';
                document.getElementById('modal-progress-bar').style.width = '0%';
                document.getElementById('results-container').style.display = 'none';
                
                document.getElementById('live-comments').innerHTML = '';
                document.getElementById('live-view-placeholder').style.display = 'block';
                document.getElementById('live-view-area').style.display = 'none';
                document.getElementById('live-doc-frame').src = 'about:blank';
                document.getElementById('live-md-frame').textContent = '';
                
                let currentFileProcessing = null;
                let currentMdProcessing = null;
                let mdPollInterval = null;
                
                let isPolling = true;
'''

with open('scratch/modal_js.txt', 'r', encoding='utf-8') as f:
    poll_js = f.read()

poll_js = poll_js.replace('const outputFormat = document.querySelector(\'input[name="output_format"]:checked\').value.toUpperCase();', 'const outputFormat = "BOTH";')
poll_js = poll_js.replace('document.getElementById(\'extra_description\').value || \'None\'', '\'None\'')

poll_js = poll_js.replace("window.location.href='/history_page.html'", "window.location.reload()")
poll_js = poll_js.replace(">View in History<", ">Reload Page<")
poll_js = poll_js.replace("closeModal()", "window.location.reload()")

content = content[:start_poll] + new_poll + poll_js + content[end_poll:]

with open('E:\\R2P_OLAP_-Online-Analytical-Processing-\\UI\\exam_detail_page.html', 'w', encoding='utf-8') as f:
    f.write(content)
