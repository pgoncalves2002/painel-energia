# Add-on do Home Assistant: o mesmo painel do projeto, em Python puro.
# A imagem oficial do Python existe para amd64, aarch64 e armv7, então serve a todas as arquiteturas do add-on.
FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt
COPY app ./app
COPY run.py ./

CMD ["python", "/srv/run.py"]
