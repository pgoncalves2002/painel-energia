#!/bin/sh
# Gera a configuração do Mosquitto a partir das variáveis de ambiente e inicia o broker.
#
#   MQTT_USERNAME + MQTT_PASSWORD definidos
#       O painel e o Home Assistant entram com esse usuário e senha (acesso completo).
#       MQTT_METER_ANONYMOUS=true (padrão): quem conecta SEM login só consegue ENVIAR mensagens
#           no tópico do medidor (MQTT_TOPIC_IN) e não lê nada. Assim o medidor funciona mesmo
#           que a tela dele não tenha campos de usuário e senha para o MQTT.
#       MQTT_METER_ANONYMOUS=false: ninguém conecta sem login.
#   sem usuário e senha
#       Broker aberto: qualquer aparelho da rede lê e escreve (só em rede doméstica confiável).
set -eu

DATA_DIR="${MOSQUITTO_DATA_DIR:-/mosquitto/data}"
CONF="${MOSQUITTO_CONF:-/tmp/mosquitto.conf}"
PORT="${MOSQUITTO_PORT:-1883}"
TOPICS="${MQTT_TOPIC_IN:-medidor/energia}"
PASSWD="$DATA_DIR/passwd"
ACL="$DATA_DIR/acl"

AUTH=0
if [ -n "${MQTT_USERNAME:-}" ] && [ -n "${MQTT_PASSWORD:-}" ]; then
  AUTH=1
fi
ANON=0
case "${MQTT_METER_ANONYMOUS:-true}" in
  1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Ss][Ii][Mm]|[Oo][Nn]|[Ss]|[Yy]) ANON=1 ;;
esac

# Tira os espaços do início e do fim (só com recursos do próprio shell).
trim() {
  _t="$1"
  while :; do case "$_t" in " "*) _t="${_t# }" ;; *) break ;; esac; done
  while :; do case "$_t" in *" ") _t="${_t% }" ;; *) break ;; esac; done
  printf '%s' "$_t"
}

mkdir -p "$DATA_DIR"

{
  echo "# Gerado por entrypoint.sh a cada inicialização; não edite."
  echo "listener $PORT"
  echo "persistence true"
  echo "persistence_location $DATA_DIR/"
  echo "log_dest stdout"
  echo "log_type error"
  echo "log_type warning"
  echo "log_type notice"
  echo "connection_messages true"
  if [ "$AUTH" = 1 ]; then
    echo "password_file $PASSWD"
    if [ "$ANON" = 1 ]; then
      echo "allow_anonymous true"
      echo "acl_file $ACL"
    else
      echo "allow_anonymous false"
    fi
  else
    echo "allow_anonymous true"
  fi
} > "$CONF"
chmod 644 "$CONF"

rm -f "$PASSWD" "$ACL"
if [ "$AUTH" = 1 ]; then
  mosquitto_passwd -b -c "$PASSWD" "$MQTT_USERNAME" "$MQTT_PASSWORD"
  chmod 600 "$PASSWD"
  if [ "$ANON" = 1 ]; then
    {
      echo "# Gerado por entrypoint.sh a cada inicialização; não edite."
      echo "# Sem login (o medidor): só envia leituras, não lê nada."
      set -f                      # os tópicos podem ter curingas; não expandir como nomes de arquivo
      OLD_IFS="$IFS"
      IFS=','
      for t in $TOPICS; do
        t="$(trim "$t")"
        if [ -n "$t" ]; then
          echo "topic write $t"
        fi
      done
      IFS="$OLD_IFS"
      set +f
      echo ""
      echo "# Com login (painel e Home Assistant): acesso completo."
      echo "user $MQTT_USERNAME"
      echo "topic readwrite #"
      echo "topic read \$SYS/#"
    } > "$ACL"
    chmod 600 "$ACL"
    echo "entrypoint: broker com login (usuário '$MQTT_USERNAME'); sem login só é aceito o envio de leituras em: $TOPICS"
  else
    echo "entrypoint: broker com login obrigatório (usuário '$MQTT_USERNAME')."
  fi
else
  echo "entrypoint: broker SEM login (defina MQTT_USERNAME e MQTT_PASSWORD no .env para exigir senha)."
fi

# O Mosquitto inicia como root e passa a rodar como o usuário "mosquitto".
if [ "$(id -u)" = "0" ] && id mosquitto >/dev/null 2>&1; then
  chown -R mosquitto:mosquitto "$DATA_DIR" 2>/dev/null || true
fi

exec mosquitto -c "$CONF"
