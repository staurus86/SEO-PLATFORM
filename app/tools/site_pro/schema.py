"""Schema for Site Audit Pro normalized result."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


AuditMode = Literal["quick", "full"]


class SiteAuditProIssue(BaseModel):
    severity: Literal["critical", "warning", "info"] = "info"
    code: str
    title: str
    details: Optional[str] = None


class SiteAuditProSummary(BaseModel):
    total_pages: int = 0
    internal_pages: int = 0
    issues_total: int = 0
    critical_issues: int = 0
    warning_issues: int = 0
    info_issues: int = 0
    score: Optional[float] = None
    mode: AuditMode = "quick"


class SiteAuditProResult(BaseModel):
    task_type: str = "site_audit_pro"
    url: str
    mode: AuditMode = "quick"
    completed_at: str
    results: Dict[str, Any] = Field(default_factory=dict)


class NormalizedSiteAuditRow(BaseModel):
    url: str
    final_url: Optional[str] = None
    in_sitemap: bool = False
    sitemap_source: Optional[str] = None
    status_code: Optional[int] = None
    status_line: Optional[str] = None
    response_time_ms: Optional[int] = None
    response_headers_count: int = 0
    html_size_bytes: Optional[int] = None
    content_kb: Optional[float] = None
    dom_nodes_count: Optional[int] = None
    js_assets_count: int = 0
    css_assets_count: int = 0
    render_blocking_js_count: int = 0
    preload_hints_count: int = 0
    perf_light_score: Optional[float] = None
    redirect_count: int = 0
    is_https: Optional[bool] = None
    compression_enabled: Optional[bool] = None
    compression_algorithm: Optional[str] = None
    cache_enabled: Optional[bool] = None
    cache_control: Optional[str] = None
    last_modified: Optional[str] = None
    content_freshness_days: Optional[int] = None
    indexable: Optional[bool] = None
    indexability_reason: Optional[str] = None
    health_score: Optional[float] = None
    title: Optional[str] = None
    title_tags_count: int = 0
    title_len: Optional[int] = None
    meta_description: Optional[str] = None
    meta_description_tags_count: int = 0
    description_len: Optional[int] = None
    charset_declared: Optional[bool] = None
    viewport_declared: Optional[bool] = None
    multiple_meta_robots: Optional[bool] = None
    canonical: Optional[str] = None
    canonical_status: Optional[str] = None
    canonical_target_status: Optional[int] = None
    canonical_target_indexable: Optional[bool] = None
    canonical_conflict: Optional[str] = None
    meta_robots: Optional[str] = None
    x_robots_tag: Optional[str] = None
    breadcrumbs: Optional[bool] = None
    structured_data: Optional[int] = None
    structured_data_detail: Dict[str, int] = Field(default_factory=dict)
    structured_types: List[str] = Field(default_factory=list)
    structured_errors_count: int = 0
    structured_error_codes: List[str] = Field(default_factory=list)
    schema_count: int = 0
    hreflang_count: int = 0
    hreflang_langs: List[str] = Field(default_factory=list)
    hreflang_has_x_default: Optional[bool] = None
    hreflang_targets: Dict[str, str] = Field(default_factory=dict)
    hreflang_issues: List[str] = Field(default_factory=list)
    mobile_friendly_hint: Optional[bool] = None
    word_count: Optional[int] = None
    unique_word_count: Optional[int] = None
    keyword_stuffing_score: Optional[float] = None
    top_keywords: List[str] = Field(default_factory=list)
    keyword_density_profile: Dict[str, float] = Field(default_factory=dict)
    lexical_diversity: Optional[float] = None
    readability_score: Optional[float] = None
    avg_sentence_length: Optional[float] = None
    avg_word_length: Optional[float] = None
    complex_words_percent: Optional[float] = None
    content_density: Optional[float] = None
    boilerplate_percent: Optional[float] = None
    toxicity_score: Optional[float] = None
    filler_ratio: Optional[float] = None
    heading_distribution: Dict[str, int] = Field(default_factory=dict)
    semantic_tags_count: Optional[int] = None
    html_quality_score: Optional[float] = None
    deprecated_tags: List[str] = Field(default_factory=list)
    hidden_content: Optional[bool] = None
    hidden_nodes_count: Optional[int] = None
    hidden_text_chars: Optional[int] = None
    hidden_text_snippets: List[str] = Field(default_factory=list)
    cta_count: Optional[int] = None
    lists_count: Optional[int] = None
    tables_count: Optional[int] = None
    faq_count: Optional[int] = None
    h1_count: Optional[int] = None
    h1_text: Optional[str] = None
    h_hierarchy: Optional[str] = None
    h_errors: List[str] = Field(default_factory=list)
    h_details: Dict[str, Any] = Field(default_factory=dict)
    images_count: Optional[int] = None
    images_without_alt: Optional[int] = None
    images_no_alt: Optional[int] = None
    images_modern_format_count: int = 0
    images_external_count: int = 0
    image_duplicate_src_count: int = 0
    generic_alt_count: int = 0
    decorative_non_empty_alt_count: int = 0
    images_optimization: Dict[str, int] = Field(default_factory=dict)
    external_nofollow_links: int = 0
    external_follow_links: int = 0
    follow_links_total: int = 0
    nofollow_links_total: int = 0
    outgoing_internal_links: int = 0
    incoming_internal_links: int = 0
    click_depth: Optional[int] = None
    outgoing_external_links: int = 0
    total_links: int = 0
    orphan_page: Optional[bool] = None
    topic_hub: Optional[bool] = None
    pagerank: Optional[float] = None
    topic_label: Optional[str] = None
    top_terms: List[str] = Field(default_factory=list)
    duplicate_title_count: int = 0
    duplicate_description_count: int = 0
    near_duplicate_count: int = 0
    near_duplicate_urls: List[str] = Field(default_factory=list)
    weak_anchor_ratio: Optional[float] = None
    anchor_text_quality_score: Optional[float] = None
    link_authority_score: Optional[float] = None
    link_quality_score: Optional[float] = None
    ai_markers_count: int = 0
    ai_markers_list: List[str] = Field(default_factory=list)
    ai_marker_sample: Optional[str] = None
    ai_markers_density_1k: Optional[float] = None
    ai_risk_score: Optional[float] = None
    ai_risk_level: Optional[str] = None
    ai_false_positive_guard: Optional[bool] = None
    page_type: Optional[str] = None
    filler_phrases: List[str] = Field(default_factory=list)
    unique_percent: Optional[float] = None
    og_tags: Optional[int] = None
    js_dependence: Optional[bool] = None
    has_main_tag: Optional[bool] = None
    cloaking_detected: Optional[bool] = None
    has_contact_info: Optional[bool] = None
    has_legal_docs: Optional[bool] = None
    has_author_info: Optional[bool] = None
    has_reviews: Optional[bool] = None
    trust_badges: Optional[bool] = None
    trust_score: Optional[float] = None
    eeat_score: Optional[float] = None
    eeat_components: Dict[str, float] = Field(default_factory=dict)
    cta_text_quality: Optional[float] = None
    tf_idf_keywords: Dict[str, float] = Field(default_factory=dict)
    semantic_links: List[Dict[str, Any]] = Field(default_factory=list)
    all_issues: List[str] = Field(default_factory=list)
    compression: Optional[bool] = None
    recommendation: Optional[str] = None
    url_params_count: int = 0
    path_depth: int = 0
    crawl_budget_risk: Optional[str] = None
    csp_present: Optional[bool] = None
    hsts_present: Optional[bool] = None
    x_frame_options_present: Optional[bool] = None
    referrer_policy_present: Optional[bool] = None
    permissions_policy_present: Optional[bool] = None
    mixed_content_count: int = 0
    security_headers_score: Optional[float] = None
    issues: List[SiteAuditProIssue] = Field(default_factory=list)


class NormalizedSiteAuditPayload(BaseModel):
    mode: AuditMode = "quick"
    summary: SiteAuditProSummary = Field(default_factory=SiteAuditProSummary)
    rows: List[NormalizedSiteAuditRow] = Field(default_factory=list)
    artifacts: Dict[str, Any] = Field(default_factory=dict)
