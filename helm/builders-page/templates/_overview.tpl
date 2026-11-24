{{/* THE THREE BUILDER DASHBOARDS (/portal-builder, /blueprint-builder, /controller-builder), in the
     layout /incidents and /compositions have (mockup A4). One entry per builder; every template that
     draws a dashboard ranges over this, so the three pages are one design with three vocabularies.

       source     the define (builders.<source>Api / builders.<source>Filter) that reads what this
                  builder published: the same steps and the same jq its deliverables RESTAction runs,
                  so the dashboard and that RESTAction cannot disagree about a row's status;
       draftKind  the draft records it owns (portal.draftBuilders, restaction.my-drafts);
       route      the page, the base of every chip's route;
       noun       one thing it builds, for the sentences;
       live       what its last, green state is called;
       views      the list card's views past Summary and Full list, each a chip and a ?view= value
                  (the Controller Builder's RestDefinitions: every one on the cluster, what the old
                  registry card listed);
       tiles      the five figures, in order: a title, the states it counts, a tone, and a caption
                  (a fixed sentence, or one of the computed captions the RESTAction names: oldest,
                  readyDrafts, merged, market, kinds). A figure links to the chip of its state, or to
                  Needs attention when it counts several that are all in it, else to All.

     THE STATES, one lifecycle and one colour each (builder-overview, by triage order):
       failed  red    a publish that failed, or a controller whose RestDefinition is failing;
       action  orange a person's step is due: install, register, or configure credentials;
       ready   orange a draft previewed and not yet published;
       draft   gray   a draft not previewed yet;
       review  blue   a change request open, a branch pushed, a definition still reconciling;
       merged  green  merged, nothing left to do here;
       live    green  registered (blueprint), Ready (controller);
       closed  gray   closed without merging.
     Needs attention, the default chip, is the first four. */}}
{{- define "builders.overviews" -}}
portal:
  source: builderPrs
  draftKind: page
  route: /portal-builder
  title: Portal Builder
  noun: page
  live: Live
  cardTitle: Pages
  tiles:
    - { title: Publish failed, states: [failed], tone: failed, caption: "no change request opened" }
    - { title: Ready to publish, states: [ready], tone: ready, caption: "previewed, not published" }
    - { title: In review, states: [review], tone: review, caption: "oldest" }
    - { title: Drafts, states: [draft], tone: draft, caption: "only you see these" }
    - { title: Merged, states: [merged, action], tone: live, caption: "merged" }
blueprint:
  source: blueprintDeliverables
  draftKind: blueprint
  route: /blueprint-builder
  title: Blueprint Builder
  noun: blueprint
  live: Registered
  cardTitle: Blueprints
  tiles:
    - { title: Failed, states: [failed], tone: failed, caption: "the publish failed" }
    - { title: To install, states: [action], tone: action, caption: "merged, not installed yet" }
    - { title: In review, states: [review], tone: review, caption: "oldest" }
    - { title: Drafts, states: [draft, ready], tone: draft, caption: "readyDrafts" }
    - { title: Registered, states: [live], tone: live, caption: "market" }
controller:
  source: controllerDeliverables
  draftKind: controller
  route: /controller-builder
  title: Controller Builder
  noun: controller
  live: Ready
  cardTitle: Controllers
  views:
    - { key: restdefs, label: RestDefinitions }
  tiles:
    - { title: Failed, states: [failed], tone: failed, caption: "a definition or the publish failed" }
    - { title: Needs a step, states: [action], tone: action, caption: "register, install or credentials" }
    - { title: In review, states: [review], tone: review, caption: "oldest" }
    - { title: Drafts, states: [draft, ready], tone: draft, caption: "readyDrafts" }
    - { title: Ready, states: [live], tone: live, caption: "kinds" }
{{- end -}}

{{/* The keys every widget of a builder dashboard declares: the page's filter params
     (krateo.io/filter-keys on its root). One it does not declare makes snowplow decline to cache it
     (F6, scripts/lint-keyextras.py). */}}
{{- define "builders.overviewKeys" -}}
[state, range, view, q]
{{- end -}}

{{/* A dashboard widget's spec head: its keys, the caller's identity (the drafts are the caller's
     own, so every reader of builder-overview asks as the same person, and its cache cell is
     theirs), and the overview it reads. Call with (dict "ctx" $ "key" $key). */}}
{{- define "builders.overviewReader" -}}
keyExtras: {{ include "builders.overviewKeys" .ctx }}
identityContext: [username]
apiRef:
  name: {{ .key }}-builder-overview
  namespace: {{ .ctx.Values.namespace }}
{{- end -}}

{{/* A widget ref in resourcesRefs. Call with (dict "ctx" $ "id" "x" "resource" "cards"). */}}
{{- define "builders.ref" -}}
- id: {{ .id }}
  apiVersion: widgets.templates.krateo.io/v1beta1
  name: {{ .id }}
  namespace: {{ .ctx.Values.namespace }}
  resource: {{ .resource }}
  verb: GET
{{- end -}}

{{/* The table a builder's own view shows (builders.overviews views). */}}
{{- define "builders.viewWidget" -}}
{{- if eq . "restdefs" }}kog-restdefinitions{{ else }}{{ fail (printf "builders.viewWidget: no table for the view %q" .) }}{{ end }}
{{- end -}}
