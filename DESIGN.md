# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-08-05
- Primary product surfaces: Vuzix Blade 2 기록·질문 UI, 로컬 웹 Wiki, Obsidian 볼트
- Evidence reviewed: `README.md`, `android/app/src/main/.../MainActivity.kt`, `backend/recall/api.py`, `backend/recall/vault/`, mock vault Markdown

## Brand
- Personality: 조용하고 신뢰할 수 있는 개인 기억 보조 도구
- Trust signals: 답변 근거, 날짜·타임스탬프, 원본 기록 연결
- Avoid: 장식적인 대시보드, 과도한 색상, 소셜 피드 같은 밀도

## Product goals
- Goals: 기록을 빠르게 찾고, 연결된 사람·주제·세션을 탐색하고, 근거 원문을 읽는다.
- Non-goals: Obsidian 플러그인 호환, Markdown 편집기, 협업·계정 시스템
- Success signals: `/`에서 검색 후 두 번 이내 조작으로 원하는 문서를 연다.

## Personas and jobs
- Primary personas: 자신의 글래스 기록을 돌아보는 단일 사용자
- User jobs: 최근 세션 확인, 사람·주제별 회상, Wiki 링크 탐색
- Key contexts of use: PC 로컬 브라우저, 같은 네트워크의 휴대폰 브라우저

## Information architecture
- Primary navigation: 좌우 슬라이드 사이드바의 파일/세션 전환, Vault 폴더 트리, 그래프/선택 문서 탭, 검색
- Core routes/screens: `/` 단일 화면, `/recall/query` 질의 API
- Content hierarchy: Vault 폴더 트리 → 전체 연결 그래프 → 선택 문서 본문

## Design principles
- 전체 연결 그래프를 기본 탐색 화면으로 두고 문서 읽기로 이어진다.
- 로컬 파일과 기존 FastAPI를 그대로 사용한다.
- Tradeoffs: 초기 버전은 읽기 전용이며 Obsidian 전체 기능을 복제하지 않는다.

## Visual language
- Color: 사용자가 선택하는 Light/Dark 작업 공간, 얇은 연결선, 문서 종류별 절제된 노드 색상
- Typography: 시스템 산세리프, 본문은 읽기 폭 제한
- Spacing/layout rhythm: 8px 기준, 데스크톱은 도구 레일·Vault 트리·대형 그래프, 모바일은 1열
- Shape/radius/elevation: 얕은 테두리와 작은 반경, 그림자 최소화
- Motion: 그래프 선택과 패널 전환만 짧게
- Imagery/iconography: 텍스트 라벨 중심, 장식 이미지 없음

## Components
- Existing components to reuse: FastAPI 앱, Vault loader, Markdown 문서 계약
- New/changed components: Wiki router, Vault 폴더 트리, 그래프 탭, 선택한 노드 이름과 닫기 버튼을 표시하는 문서 탭, 대형 링크 그래프, 그래프 설정 패널
- Variants and states: 선택·검색 결과 없음·빈 볼트·서버 오류
- Token/component ownership: Wiki CSS 파일이 웹 화면 토큰을 소유한다.

## Accessibility
- Target standard: WCAG 2.1 AA 수준의 기본 대비와 의미 구조
- Keyboard/focus behavior: 검색과 문서 버튼은 기본 탭 순서와 포커스 표시 유지
- Contrast/readability: 본문 최대 폭과 충분한 명도 차
- Screen-reader semantics: `nav`, `main`, 문서 이름을 포함한 닫기 버튼 라벨, 그래프 대체 설명 제공
- Reduced motion and sensory considerations: `prefers-reduced-motion`에서 전환 제거

## Responsive behavior
- Supported breakpoints/devices: 360px 이상 휴대폰, 태블릿, 데스크톱
- Layout adaptations: 데스크톱 사이드바는 접으면 왼쪽으로 완전히 빠지고 그래프가 확장, 모바일은 목록·문서·그래프 탭
- Touch/hover differences: 주요 터치 대상 최소 44px, hover 없이도 상태가 보인다.

## Interaction states
- Loading: 짧은 로딩 문구
- Empty: 볼트에 Markdown을 추가하라는 안내
- Error: 서버 연결 실패와 재시도 버튼
- Success: 선택 문서 본문과 연결 문서 표시
- Disabled: 결과 없는 필터는 빈 상태로 표시
- Offline/slow network: 초기 로드 후 현재 문서는 브라우저 메모리에서 탐색

## Content voice
- Tone: 간결하고 사실 중심
- Terminology: 기록, 세션, 사람, 주제, 날짜, 연결
- Microcopy rules: 기술 용어보다 사용자가 찾는 대상을 먼저 쓴다.

## Implementation constraints
- Framework/styling system: 기존 FastAPI + 순수 HTML/CSS/JavaScript
- Design-token constraints: Wiki CSS 내부 변수만 사용
- Performance constraints: 로컬 개인 볼트 규모에서는 전체 문서를 한 번에 읽는다.
- Compatibility constraints: 최신 Chrome·Edge·Android WebView
- Test/screenshot expectations: API 계약 테스트와 JavaScript 문법 검사

## Open questions
- [ ] 향후 Markdown 편집이 필요하면 Obsidian을 열 것인지 웹 편집기를 추가할지 결정
- [ ] 실제 폰 앱 안에 WebView로 넣을지 브라우저 링크로 둘지 실기기에서 결정
