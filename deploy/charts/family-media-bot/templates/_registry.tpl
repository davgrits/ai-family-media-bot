{{/*
Shared fragments so the web and worker deployments cannot drift apart in how they
mount the registry or how they are triggered to restart.

The checksums hash the *rendered templates*, not .Files.Get directly, so a change
to a ConfigMap's name, labels, or namespace also rolls the pods — not only a
change to its payload.

Without these annotations a ConfigMap-only change deploys green and never reaches
the pods: the ConfigMap updates, the pod template does not, so the Deployment
controller sees nothing to roll. That is the defect three separate reviews found.
*/}}
{{- define "family-media-bot.configChecksums" -}}
checksum/registry: {{ include (print $.Template.BasePath "/configmap-registry.yaml") . | sha256sum }}
checksum/config: {{ include (print $.Template.BasePath "/configmap-env.yaml") . | sha256sum }}
{{- end }}

{{/*
No subPath: with it the kubelet never propagates ConfigMap updates to the file.
The app reads at startup so the refresh alone would not take effect today, but
avoiding subPath costs nothing and leaves a file-watch reload possible later.
*/}}
{{- define "family-media-bot.registryVolume" -}}
- name: registry
  configMap:
    name: {{ include "family-media-bot.name" . }}-registry
{{- end }}

{{- define "family-media-bot.registryMount" -}}
- name: registry
  mountPath: {{ .Values.registry.mountPath }}
  readOnly: true
{{- end }}

{{- define "family-media-bot.registryEnv" -}}
- name: CHARACTERS_FILE
  value: {{ printf "%s/characters.yaml" .Values.registry.mountPath | quote }}
- name: CHARACTERS_REQUIRED
  value: {{ .Values.registry.required | quote }}
{{- end }}
