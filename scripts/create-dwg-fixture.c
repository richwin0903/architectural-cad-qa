/* Synthetic geometry acceptance fixture, created through GNU LibreDWG's C API.
 * Build in the isolated LibreDWG builder image, not in the running app:
 * gcc -I/build/include create-dwg-fixture.c /build/src/.libs/libredwg.a -lm -o create-dwg-fixture
 * ./create-dwg-fixture architectural-demo.dwg
 * This is a fabricated floor plan, never a real architectural area assertion.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <iconv.h>
#include "dwg.h"
#include "dwg_api.h"

static void fail(const char *message) { fprintf(stderr, "%s\n", message); exit(1); }

static Dwg_Object_BLOCK_HEADER *model(Dwg_Data *dwg) {
  Dwg_Object *obj = dwg_model_space_object(dwg);
  if (!obj) fail("No model space");
  return obj->tio.object->tio.BLOCK_HEADER;
}

static void layer(Dwg_Data *dwg, const char *name) {
  BITCODE_H existing = dwg_find_tablehandle(dwg, name, "LAYER");
  if (existing) {
    dwg->header_vars.CLAYER = dwg_add_handleref(dwg, 5, existing->absolute_ref, NULL);
    return;
  }
  int error = 0;
  Dwg_Object_LAYER *entry = dwg_add_LAYER(dwg, name);
  Dwg_Object *obj = dwg_obj_generic_to_object(entry, &error);
  if (!entry || error || !obj) fail("Could not add layer");
  /* A layer's ACI must be 1..255. LibreDWG's add API leaves it at 0
   * (ByBlock), which is invalid for layers and renders white on white. */
  entry->color.index = strcmp(name, "DOOR") == 0 ? 1 :
                       strcmp(name, "ROOM_BOUNDARY") == 0 ? 3 : 7;
  entry->plotflag = 1;
  entry->ltype = dwg_add_handleref(dwg, 5, 0x16, NULL); /* CONTINUOUS */
  dwg->header_vars.CLAYER = dwg_add_handleref(dwg, 5, obj->handle.value, NULL);
}

static void box(Dwg_Data *dwg, const char *name, double x1, double y1, double x2, double y2) {
  layer(dwg, name);
  dwg_point_2d points[] = {{x1,y1},{x2,y1},{x2,y2},{x1,y2}};
  Dwg_Entity_LWPOLYLINE *poly = dwg_add_LWPOLYLINE(model(dwg), 4, points);
  if (!poly) fail("Could not add closed contour");
  poly->flag |= 512;
}

static void text(Dwg_Data *dwg, const char *value, double x, double y, double height) {
  dwg_point_3d point = {x,y,0};
  /* LibreDWG's pre-2007 add API copies bytes without transcoding UTF-8.
   * Explicit GBK matches header.codepage=CP_ANSI_936 for this R2000 fixture. */
  char encoded[2048] = {0}, *out = encoded, *in = (char *)value;
  size_t in_left = strlen(value), out_left = sizeof(encoded) - 1;
  iconv_t converter = iconv_open("GBK", "UTF-8");
  if (converter == (iconv_t)-1
      || iconv(converter, &in, &in_left, &out, &out_left) == (size_t)-1)
    fail("Could not encode fixture label");
  iconv_close(converter);
  Dwg_Entity_TEXT *label = dwg_add_TEXT(model(dwg), encoded, &point, height);
  if (!label) fail("Could not add label");
  label->width_factor = 1.0;
}

int main(int argc, char **argv) {
  if (argc != 2) fail("Expected output DWG path");
  Dwg_Data *dwg = dwg_new_Document(R_2000, 0, 0);
  if (!dwg) fail("Could not initialize document");
  dwg->header.from_version = R_2000;
  dwg->header.codepage = 39; /* CP_ANSI_936 / GBK */
  /* The add API sets both import flags. Strings below are already GBK;
   * INJSON would transcode those bytes a second time as though UTF-8. */
  dwg->opts &= ~DWG_OPTS_INJSON;
  dwg->header_vars.INSUNITS = 4; /* millimetres */
  box(dwg, "WALL", 0, 0, 10000, 6000);
  box(dwg, "ROOM_BOUNDARY", 0, 2000, 4000, 6000);
  box(dwg, "ROOM_BOUNDARY", 4000, 2000, 10000, 6000);
  box(dwg, "ROOM_BOUNDARY", 0, 0, 10000, 2000);
  layer(dwg, "ROOM_LABEL");
  text(dwg, "会议室", 1550, 4000, 280);
  text(dwg, "办公室", 6550, 4000, 280);
  text(dwg, "走廊", 4550, 1000, 280);
  layer(dwg, "NOTES");
  text(dwg, "二层平面图 / SYNTHETIC DWG DEMO", 0, 7100, 330);
  text(dwg, "单位：mm，合成测试图纸，非真实建筑项目", 0, 6600, 190);
  text(dwg, "闭合轮廓为预设测试边界；门窗为标准图块", 0, -800, 180);

  layer(dwg, "DOOR");
  Dwg_Object_BLOCK_HEADER *door = dwg_add_BLOCK_HEADER(dwg, "DOOR_M0921");
  if (!door || !dwg_add_BLOCK(door, "DOOR_M0921")) fail("Could not add door block");
  dwg_point_3d zero = {0,0,0}, end = {0,900,0};
  if (!dwg_add_LINE(door, &zero, &end)) fail("Could not draw door");
  if (!dwg_add_ARC(door, &zero, 900, 0, 1.5707963267948966)) fail("Could not draw door arc");
  if (!dwg_add_ENDBLK(door)) fail("Could not close door block");
  dwg_point_3d first = {1000,2000,0}, second = {6000,2000,0};
  if (!dwg_add_INSERT(model(dwg), &first, "DOOR_M0921", 1,1,1,0)
      || !dwg_add_INSERT(model(dwg), &second, "DOOR_M0921", 1,1,1,0)) fail("Could not place doors");
  text(dwg, "M0921", 1000, 1600, 160);
  text(dwg, "M0921", 6000, 1600, 160);
  fprintf(stderr, "Model entries %u first %llx last %llx opts %u versions %u/%u\n",
      model(dwg)->num_owned,
      (unsigned long long)(model(dwg)->first_entity ? model(dwg)->first_entity->absolute_ref : 0),
      (unsigned long long)(model(dwg)->last_entity ? model(dwg)->last_entity->absolute_ref : 0),
      dwg->opts, dwg->header.version, dwg->header.from_version);
  int result = dwg_write_file(argv[1], dwg);
  dwg_free(dwg);
  free(dwg);
  if (result >= DWG_ERR_CRITICAL) fail("Could not write synthetic DWG");
  return 0;
}
