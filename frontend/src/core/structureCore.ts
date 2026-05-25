import structureCoreManifestJson from '../../../core/structure_core.json';

export interface ProductSurface {
  id: 'cli_tui' | 'desktop_app' | 'web_app';
  name: string;
  entrypoint: string;
  audience: string;
  runtime_model: string;
  primary_jobs: string[];
  boundary: string;
}

export interface PaperAnchor {
  system_name: string;
  thesis: string;
  sections: string[];
}

export interface CoreFlowStep {
  id: string;
  name: string;
  description: string;
}

export interface BenchmarkReportSchema {
  id: string;
  family: string;
  description: string;
  required_fields: string[];
  cost_fields: string[];
  evidence_fields: string[];
}

export interface CapabilitySurfaceStatus {
  surface_id: ProductSurface['id'];
  status: 'native' | 'service_native' | 'read_only' | string;
  entrypoint: string;
  evidence: string;
}

export interface CoreCapability {
  id: string;
  name: string;
  primitive_id: string;
  description: string;
  surface_status: CapabilitySurfaceStatus[];
}

export interface SurfaceRole {
  surface_id: ProductSurface['id'];
  role: string;
}

export interface CorePrimitive {
  id: string;
  name: string;
  paper_section: string;
  invariant: string;
  implementation_contract: string;
  surface_roles: SurfaceRole[];
}

export interface StructureCoreManifest {
  schema_version: string;
  paper_anchor: PaperAnchor;
  canonical_flow: CoreFlowStep[];
  benchmark_report_schema: BenchmarkReportSchema;
  capabilities: CoreCapability[];
  primitives: CorePrimitive[];
  surfaces: ProductSurface[];
}

export const STRUCTURE_CORE_MANIFEST =
  structureCoreManifestJson as StructureCoreManifest;

export const STRUCTURE_CORE_SURFACES = STRUCTURE_CORE_MANIFEST.surfaces;

export const WEB_SURFACE = getSurface('web_app');

export function getSurface(surfaceId: ProductSurface['id']): ProductSurface {
  const surface = STRUCTURE_CORE_MANIFEST.surfaces.find(
    (candidate) => candidate.id === surfaceId,
  );
  if (!surface) {
    throw new Error(`Unknown Structure surface: ${surfaceId}`);
  }
  return surface;
}

export function getCorePrimitive(primitiveId: string): CorePrimitive {
  const primitive = STRUCTURE_CORE_MANIFEST.primitives.find(
    (candidate) => candidate.id === primitiveId,
  );
  if (!primitive) {
    throw new Error(`Unknown Structure core primitive: ${primitiveId}`);
  }
  return primitive;
}

export function getCoreCapability(capabilityId: string): CoreCapability {
  const capability = STRUCTURE_CORE_MANIFEST.capabilities.find(
    (candidate) => candidate.id === capabilityId,
  );
  if (!capability) {
    throw new Error(`Unknown Structure core capability: ${capabilityId}`);
  }
  return capability;
}
