$fn = 32;

// ==========================================================
// SKRUVHÅLSTEST — 1.7 mm till 3.0 mm
// 14 hålstorlekar × 2 exemplar
// ==========================================================

hole_ds = [
    1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3,
    2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3.0
];

cols = len(hole_ds);
rows = 2;

spacing_x = 12;
spacing_y = 14;

base_margin = 8;
base_t = 2.0;

post_d = 8.0;
post_h = 8.0;

// liten försänkning/markering runt toppen
top_marker_d = 5.5;
top_marker_depth = 0.4;

// text
label_size = 3.2;
label_depth = 0.35;

// Total storlek
base_w = (cols - 1) * spacing_x + 2 * base_margin;
base_h = (rows - 1) * spacing_y + 2 * base_margin + 10;


// ==========================================================
// HJÄLPMODULER
// ==========================================================

module post_with_hole(hole_d) {
    difference() {
        union() {
            cylinder(h = post_h, d = post_d);

            // liten fot så pelaren sitter bra i basen
            cylinder(h = 1.2, d = post_d + 2);
        }

        // hål genom hela pelaren och lite ner i basen
        translate([0, 0, -0.5])
            cylinder(h = post_h + base_t + 2, d = hole_d);

        // tunn visuell toppmarkering
        translate([0, 0, post_h - top_marker_depth])
            cylinder(h = top_marker_depth + 0.1, d = top_marker_d);
    }
}

module engraved_text(txt) {
    linear_extrude(height = label_depth)
        text(
            txt,
            size = label_size,
            halign = "center",
            valign = "center"
        );
}


// ==========================================================
// MODELL
// ==========================================================

difference() {
    union() {
        // basplatta
        cube([base_w, base_h, base_t]);

        // pelare
        for (row = [0 : rows - 1]) {
            for (col = [0 : cols - 1]) {
                x = base_margin + col * spacing_x;
                y = base_margin + row * spacing_y;

                translate([x, y, base_t])
                    post_with_hole(hole_ds[col]);
            }
        }
    }

    // graverade labels längst fram
    for (col = [0 : cols - 1]) {
        x = base_margin + col * spacing_x;
        y = base_h - 5;

        translate([x, y, base_t - label_depth + 0.01])
            engraved_text(str(hole_ds[col]));
    }

    // radmarkeringar 1, 2, 3 till vänster
    for (row = [0 : rows - 1]) {
        x = 3.5;
        y = base_margin + row * spacing_y;

        translate([x, y, base_t - label_depth + 0.01])
            engraved_text(str(row + 1));
    }
}