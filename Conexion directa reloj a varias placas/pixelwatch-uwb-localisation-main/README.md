# UWB Indoor Localisation with Pixel Watch 3

This repository contains the core scripts used in the article *[insert article title]* for processing UWB ranging data collected from a Pixel Watch 3 and DWM3001CDK anchors.  
The implementation focuses on evaluating **accuracy, latency, and battery consumption**, and applies a **K-Nearest Neighbours (KNN)** algorithm for zone-level localisation.  

## Features
- Data preprocessing with sliding-window segmentation.  
- Extraction of UWB distance features.  
- Zone-level localisation using KNN.  
- Evaluation of accuracy (MAE), latency, and battery usage.  

## Requirements
- Python 3.9+  
- Required libraries (install via `pip install -r requirements.txt`):
  - `numpy`
  - `pandas`
  - `scikit-learn`
  - `matplotlib`

## Data
The dataset consists of UWB ranging logs collected by a custom Wear OS application on the Pixel Watch 3.  
Each record contains:
- Timestamp  
- Distances to four anchors (cm)  
- Ground-truth zone label  
- Battery percentage  

*(Due to privacy and size constraints, raw datasets are not included. Researchers can adapt the provided scripts to their own recordings.)*

## Usage
1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/pixelwatch-uwb-localisation.git
   cd pixelwatch-uwb-localisation
