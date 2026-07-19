{{- define "family-media-bot.name" -}}
{{- default .Chart.Name .Values.app.name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "family-media-bot.labels" -}}
app.kubernetes.io/name: {{ include "family-media-bot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end }}

{{- define "family-media-bot.selectorLabels" -}}
app.kubernetes.io/name: {{ include "family-media-bot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
