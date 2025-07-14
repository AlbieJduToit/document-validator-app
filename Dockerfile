# Stage 1: Use an official, lightweight Python image as a base
FROM python:3.11-slim

# Stage 2: Install system-level dependencies required by OpenCV
# This list now includes the libraries for both graphics (libgl1) and
# general utilities/threading (libglib2.0-0).
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Stage 3: Set up the working environment inside the container
WORKDIR /app

# Stage 4: Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 5: Copy your application code
COPY . .

# Stage 6: Configure the container to run the app
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]