# R2P OLAP (Online Analytical Processing)

**⚠️ Note: This is an under-development project successor to the original R2P (Report_To_Plot).**

An automated, AI-powered tool that extracts student marks from unstructured report files (like PDFs and images) to generate analytical charts and presentation-ready PowerPoint decks.

##  Overview

Extracting marks and performance data manually from unstructured student reports is time-consuming and prone to human error. Report2Plot solves this by providing a fully automated workflow. It uses **Google Gemini** for intelligent data extraction and **Model Context Protocol (MCP)** servers to manage modular processing tasks like charting and rendering. 

### Key Features
- **Automated Data Extraction**: Convert unstructured PDFs/images to structured data using Google Gemini Vision.
- **Intelligent Caching (`Local_Mem`)**: Bypasses redundant API calls for previously processed reports, drastically reducing API costs and latency.
- **Automated Visualization**: Dynamically generates statistical charts using Matplotlib.
- **Presentation Ready**: Automatically builds downloadable PowerPoint (`.pptx`) decks summarizing student performance.
- **Web UI**: A simple, browser-based user interface for uploading files and tracking the processing pipeline.
- **History Tracking**: Keeps a log of past assignments and generated reports.
- **Modular Architecture**: Built on the Model Context Protocol (MCP) using `fastmcp`.

##  Project Structure

![Alt text](https://github.com/Longbatman09/Report2Plot/blob/5994b9aa8cea82d4cb792f42aa18f3127aa8eff8/Workflow.png)

```text
Report2Plot/
├── agents/             # Core Python logic (orchestrator, local memory manager)
├── mcp_servers/        # MCP servers (file_watcher, vision_extractor, plot_renderer)
├── UI/                 # Static HTML/JS pages for the web interface
├── Input/              # Source directory for uploaded files (PDFs, images)
├── Output/             # Destination for generated charts and .pptx files
├── Local_Mem/          # Local file system cache and history storage
├── my-agent/           # Scaffolding for generated ADK agent projects
├── scan-agent/         # Scaffolding for scanning-specific ADK agent
├── tests/              # High-level workflow tests
├── run.bat             # Windows batch script to launch the application
└── factory_reset.py    # Utility to clear Input, Output, and Local_Mem directories
```

##  How It Works (The Pipeline)

1. **Upload**: User places report files in the `Input/` folder or uploads them via the Web UI.
2. **Document Conversion**: `llmwhisperer_converter.py` converts source PDFs into structured Markdown.
3. **AI Extraction**: `mcp_servers/vision_extractor.py` uses Gemini to intelligently extract student marks and details into structured JSON.
4. **Caching**: Extracted data is cached in `Local_Mem/` to prevent redundant API usage on future runs.
5. **Rendering**: `mcp_servers/plot_renderer.py` transforms the data into Matplotlib charts and a PowerPoint presentation.
6. **Delivery**: The final output is saved to the `Output/` directory and presented in the UI.

##  Setup & Installation

### Prerequisites
- **Python 3.10+** (Recommended)
- **Windows OS** (Due to the `run.bat` launcher, though Python scripts are cross-platform)
- A **Google Gemini API Key** (Make sure to configure it in your `.env` file)

### Getting Started

1. **Clone the repository** (if you haven't already).
2. **Configure your environment**: Ensure you have an active `.env` file containing your Gemini API key (e.g., `GEMINI_API_KEY=your_key_here`).
3. **Launch the application**:
   Simply double-click or run `run.bat` from your terminal:
   ```cmd
   run.bat
   ```
   This script will automatically verify Python, install missing dependencies from `requirements.txt`, start the local orchestrator server, and open the Web UI in your default browser.

### Utilities

- **Factory Reset**: If you need to wipe all data (clear `Input/`, `Output/`, and `Local_Mem/`), you can run the factory reset script:
  ```cmd
  python factory_reset.py
  ```

##  Technologies Used

- **Languages**: Python, HTML, JavaScript
- **AI & ML**: Google Gemini, `google-genai`
- **Architecture**: `fastmcp` (Model Context Protocol), built-in Python `http.server`
- **Document Processing**: `pymupdf`, `pillow`
- **Data Visualization**: `matplotlib`
- **Reporting**: `python-pptx`

##  Future Scope
- **Cloud Database**: Migrate from local JSON file caching to a cloud database like Firebase or PostgreSQL.
- **Mobile Platform Development**: For Portable Analytics.
- **Broader Exports**: Add support for Excel exports and analytical PDF reports.
- **Shift To LLM Whisper**: For Faster report scans
- **Advanced Analytics**: Class-wide trend dashboards and predictive analytics.

##  Credits
- **Base_Software**: B.Vishal Chandrakanth 
- **Cloud and Cross Platform Implementation**: M.Kabilan 
- **Core Technologies**: Google Gemini, Model Context Protocol (MCP)

*Licencing is Not Finalized*
