# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 The HuggingFace Authors.

{{- define "envCommon" -}}
- name: COMMON_ASSETS_BASE_URL
  value: "{{ include "assets.baseUrl" . }}"
- name: COMMON_HF_ENDPOINT
  value: {{ .Values.common.hfEndpoint | quote }}
- name: COMMON_HF_TOKEN
  {{- if .Values.secrets.token.fromSecret }}
  valueFrom:
    secretKeyRef:
      {{- if eq .Values.secrets.token.secretName "" }}
      name: {{ .Release.Name }}-datasets-server-app-token
      {{- else }}
      name: {{ .Values.secrets.token.secretName | quote }}
      {{- end }}
      key: HF_TOKEN
      optional: false
  {{- else }}
  value: {{ .Values.secrets.token.value }}
  {{- end }}
- name: COMMON_LOG_LEVEL
  value: {{ .Values.common.logLevel | quote }}
{{- end -}}
