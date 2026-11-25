{{/*
  portal.marketplaceCategoryDefs — a Marketplace chart's CATEGORY. ONE rule for the catalog grid's
  tile tag and icon and its category chips (restaction.blueprints-catalog) and the chart's detail
  page's Category row (marketplace-detail), so a tile, a chip and a page cannot disagree (UX audit
  MKT-4 and MKT-5; Diego, 2026-11-25: the category worked out from keywords shows everywhere).

  THE RULE, first that applies:
    annotation  the chart's krateo.io/category, when it is one of the eight categories (any case);
    keyword     else the FIRST of its keywords, in the chart's own order, that the fixed table below
                lists, compared whole and in lower case. Exact tokens, never substrings: matching by
                substring gave every ACK operator the "kubernetes" glyph and agentgateway the
                "gateway" one by its own name;
    none        else "" — no tag, no chip, never a made-up "uncategorized".
  Over the two indexes of 2026-11-25 (927 charts) that is 318 by annotation, 394 by keyword and 215
  with none.

  marketplaceCategory: input an index entry (annotations, keywords); output its category key, lower
  case ("database"), or "". marketplaceCategoryLabel: a key as it is printed ("Database"), "" for "".
  marketplaceCategories: the eight keys.
  Emits defs only, with no jq `#` comment, so it also works in a folded (`>`) filter.
*/}}
{{- define "portal.marketplaceCategoryDefs" -}}
def marketplaceCategories: ["network", "compute", "database", "application", "security", "observability", "storage", "generator"];
def marketplaceCategoryTable: {
  "database": ["database", "sql", "rds", "dynamodb", "documentdb", "docdb", "memorydb", "dbforpostgresql", "dbformysql", "postgresql", "mysql", "mongodb", "keyspaces", "dsql", "cosmosdb", "spanner", "bigtable", "firestore", "redis", "cache", "elasticache"],
  "storage": ["storage", "s3", "efs", "backup", "snapshot", "object-storage", "filestore", "cinder"],
  "network": ["network", "vpc", "dns", "route53", "elbv2", "elb", "loadbalancer", "cloudfront", "cdn", "apigateway", "apigatewayv2", "apimanagement", "networkservices", "securitygroup", "ingress", "gateway-api", "gateway"],
  "security": ["security", "iam", "kms", "identity", "certificate", "acm", "acmpca", "secrets", "keyvault", "cert-manager", "trust-manager", "tls", "authn", "oidc", "barbican", "keycloak"],
  "compute": ["compute", "ec2", "lambda", "eks", "ecs", "containerservice", "autoscaling", "nova"],
  "observability": ["observability", "monitoring", "logging", "insights", "cloudwatch", "cloudwatchlogs", "prometheusservice", "opentelemetry", "dashboard"],
  "application": ["sagemaker", "bedrock", "bedrockagent", "bedrockagentcorecontrol", "servicebus", "eventhub", "eventbridge", "sns", "sqs", "kafka", "kinesis"],
  "generator": ["kog", "oasgen", "oasgen-provider", "codegen"] };
def marketplaceCategoryKeywords: [ marketplaceCategoryTable | to_entries[] | .key as $c | .value[] | {key: ., value: $c} ] | from_entries;
def marketplaceCategory:
  (((.annotations // {}) | if type == "object" then (.["krateo.io/category"] // "") else "" end) | tostring | ascii_downcase) as $a
  | if any(marketplaceCategories[]; . == $a) then $a
    else (marketplaceCategoryKeywords as $t
          | first(((.keywords // []) | if type == "array" then .[] else empty end) | tostring | ascii_downcase | ($t[.] // empty)) // "")
    end;
def marketplaceCategoryLabel: if . == "" or . == null then "" else (.[0:1] | ascii_upcase) + .[1:] end;
{{- end -}}
