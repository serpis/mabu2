$fn = 32;

// ==========================================================
// PARAMETRAR — JUSTERA HÄR
// ==========================================================

// Yttre referensmått: centrum på dina ursprungliga yttersta små hål
outer_w = 170.5;
outer_h = 109.75;

// Global placering, som i din gamla modell
origin = [5, 5, 0];

// Bottenplatta, centrerad i outer footprint
plate_w = 152;
plate_h = 94;
plate_t = 1.6;

plate_x = (outer_w - plate_w) / 2;
plate_y = (outer_h - plate_h) / 2;

// Svävande höjd för vägg/låda
floating_z = 4;

// Vägg
wall_t = 2;
wall_z_h = 18;

// Hörnpelare / ytterhål
corner_pillar_r = 1.5;   // motsvarar cylinder(h=5, r=1.5)
corner_hole_r   = 0.5;   // dina små hörnhål

// Väggen får exakt gå till hull av hörnpelarna
wall_outer_r = corner_pillar_r;

// Stödarmar
support_small_d = 2 * corner_pillar_r;
support_big_d   = 7.5;


// ==========================================================
// USB-A CUTOUT FÖR RPI5
// ==========================================================

// Flyttad 8 mm åt höger från tidigare x=22.
usb_a_cutout_x = 30;
usb_a_cutout_w = 40;

usb_a_cutout_y = -wall_outer_r - 1;
usb_a_cutout_z = floating_z - 1;
usb_a_cutout_h = wall_z_h + 4;

// Extra stöd på varsin sida om USB-A-cutouten.
// Dessa följer automatiskt med usb_a_cutout_x.
usb_side_beam_d = 5;
usb_side_beam_h = 5;
usb_side_beam_gap = 3;
usb_side_beam_len = 13;


// ==========================================================
// LOCKFÄSTEN — M3 DIREKT I PLAST
// ==========================================================

// Pilot hole för M3 i plast.
// Justera t.ex. 2.4–2.7 beroende på skrivare/material.
lid_screw_pilot_d = 2.5;

// Riktiga lockbossar, flyttade in på diagonalen så de inte försvinner.
// De hålls innanför ytterhullen.
lid_tower_d = 8.0;
lid_tower_h = wall_z_h;
lid_tower_inset = 7.0;


// ==========================================================
// RASPBERRY PI 5 — M2.5 DIREKT I PLAST
// ==========================================================

// Pi 5-kort: 85 x 56 mm
// Monteringshål: M2.5-ish, antagen hålbild 58 x 49 mm,
// offset 3.5 mm från hörn.
pi_size = [85, 56];

pi_holes = [
    [3.5, 3.5],
    [61.5, 3.5],
    [3.5, 52.5],
    [61.5, 52.5]
];

// Pi i porträttläge, roterad 180° mot tidigare pi_rot=90.
pi_rot = 270;

// Position justerad så footprinten blir ungefär samma område som tidigare.
pi_pos = [12, 97.5, 0];

// Pi-pelare direkt från bottenplattan
pi_standoff_base_z = plate_t;

// Sänkt från 12 mm till 9 mm
pi_standoff_h = 9;

pi_standoff_d = 7.2;

// M2.5-skruv direkt i plast.
// Testa 2.0–2.2 beroende på skrivare/material.
pi_screw_pilot_d = 2.0;


// ==========================================================
// JC3248S035 — 3.5" SPI DISPLAY, MOTSATT SIDA FRÅN RPI
// ==========================================================

// PCB 99.0 x 54.9 mm.
display_size = [99.0, 54.9];

// Display roterad 90° i XY-planet.
display_rot = 90;
display_pos = [158, 5.5, 0];

// Låst D5 från pappersmall:
// X-offset från vänster/höger kortkant = 2.75 mm
// Y-offset från under/över kortkant = 4.25 mm
display_hole_offset = [2.75, 4.25];

display_holes = [
    [display_hole_offset[0], display_hole_offset[1]],
    [display_size[0] - display_hole_offset[0], display_hole_offset[1]],
    [display_size[0] - display_hole_offset[0], display_size[1] - display_hole_offset[1]],
    [display_hole_offset[0], display_size[1] - display_hole_offset[1]]
];

// Display-pelare direkt från bottenplattan.
// Displayen ska sticka upp över locket, samma princip som kameran.
display_standoff_base_z = plate_t;

display_raise_above_lid = 2;
display_board_z = floating_z + wall_z_h + display_raise_above_lid;
display_standoff_h = display_board_z - display_standoff_base_z;

// Lite grövre eftersom pelarna blir höga
display_standoff_d = 8.0;

// M3 pilot hole direkt i plast
display_screw_pilot_d = 2.5;


// ==========================================================
// KAMERA — M2 DIREKT I PLAST
// ==========================================================

// Kamerakort: 25 mm brett, 24 mm högt
camera_size = [25, 24];

// Kamera centrerad.
camera_pos_x = outer_w / 2;
camera_y_margin = 10;

// Placera kameran mot väggens inre/övre sida.
camera_pos = [
    camera_pos_x - camera_size[0] / 2,
    outer_h + wall_outer_r - wall_t - camera_size[1] - camera_y_margin,
    0
];

// Låst K2 från pappersmall:
// Kamerahål CC = 20.75 mm i bredd, 13.50 mm i höjd
camera_hole_cc = [20.75, 13.50];

camera_hole_offset = [
    (camera_size[0] - camera_hole_cc[0]) / 2,
    (camera_size[1] - camera_hole_cc[1]) / 2
];

camera_holes = [
    [camera_hole_offset[0], camera_hole_offset[1]],
    [camera_hole_offset[0] + camera_hole_cc[0], camera_hole_offset[1]],
    [camera_hole_offset[0], camera_hole_offset[1] + camera_hole_cc[1]],
    [camera_hole_offset[0] + camera_hole_cc[0], camera_hole_offset[1] + camera_hole_cc[1]]
];

// Kamera-pelare direkt från bottenplattan.
// Kamerakortet lyfts över locknivån.
camera_standoff_base_z = plate_t;

camera_raise_above_lid = 2;
camera_board_z = floating_z + wall_z_h + camera_raise_above_lid;
camera_standoff_h = camera_board_z - camera_standoff_base_z;

camera_standoff_d = 6.5;

// Pilot hole för M2 direkt i plast.
camera_screw_pilot_d = 1.6;


// ==========================================================
// CUTOUTS
// ==========================================================

// Antenn-cutout är borttagen helt.

// Kabelhål.
// Positionen behåller gamla vänster-/nederkant.
// Storleken är +2 mm åt x+ och +2 mm åt y+.
cable_cutout_size = [44, 12, 30];
cable_cutout_pos = [
    outer_w / 2 - 42 / 2,       // behåller gamla vänsterkant från 42 mm hål
    119 - 64 - 10 + 1 - 5,      // behåller gamla nederkant
    0
];

// Top cutout skär bara bottenplattan.
top_cutout_size = [12, 12, 30];
top_cutout_pos = [
    outer_w / 2 - top_cutout_size[0] / 2,
    119 - 12 - 4 - 5,
    0
];


// ==========================================================
// DEBUG
// ==========================================================

show_pi_footprint = true;
show_display_footprint = true;
show_camera_footprint = true;
show_keepouts = true;
show_wall_footprint = false;


// ==========================================================
// HJÄLPARE
// ==========================================================

function corner_centers() = [
    [0, 0],
    [outer_w, 0],
    [outer_w, outer_h],
    [0, outer_h]
];

module wall_outer_2d() {
    hull() {
        for (p = corner_centers()) {
            translate(p)
                circle(r = wall_outer_r);
        }
    }
}

// Innerkonturen görs med offset, eftersom wall_t kan vara större än corner_pillar_r.
module wall_inner_2d() {
    offset(delta = -wall_t)
        wall_outer_2d();
}

// Klipp geometri så den aldrig sticker utanför ytterväggens hull.
// Används för beams/stöd som annars kan sticka ut p.g.a. cylinderdiametrar.
module keep_inside_outer_hull(zmin=-20, zmax=80) {
    intersection() {
        children();

        translate([0, 0, zmin])
            linear_extrude(height = zmax - zmin)
                wall_outer_2d();
    }
}

module beam(a, b, h=2.5, d=6) {
    hull() {
        translate(a)
            cylinder(h=h, d=d);

        translate(b)
            cylinder(h=h, d=d);
    }
}

module myline(from, from_h, from_d, to, to_h, to_d) {
    hull() {
        translate(from)
            cylinder(h = from_h, d = from_d);

        translate(to)
            cylinder(h = to_h, d = to_d);
    }
}

module simple_standoff(h=5, d=5.5, screw_d=2.2) {
    difference() {
        union() {
            cylinder(h=h, d=d);
            cylinder(h=1.0, d=d+2);
        }

        translate([0, 0, -0.5])
            cylinder(h=h+1, d=screw_d);
    }
}

module lid_tower() {
    difference() {
        cylinder(h=lid_tower_h, d=lid_tower_d);

        // M3 pilot hole direkt i plast
        translate([0, 0, -0.5])
            cylinder(h=lid_tower_h+1, d=lid_screw_pilot_d);
    }
}


// ==========================================================
// CUTOUT-MODULER
// ==========================================================

module usb_a_cutout_volume() {
    translate([
        usb_a_cutout_x,
        usb_a_cutout_y,
        usb_a_cutout_z
    ])
        cube([
            usb_a_cutout_w,
            wall_outer_r + wall_t + 4,
            usb_a_cutout_h
        ]);
}

module cable_cutout_volume() {
    translate(cable_cutout_pos)
        cube(cable_cutout_size);
}


// ==========================================================
// BOTTENPLATTA
// ==========================================================

module base_plate() {
    difference() {
        translate([plate_x, plate_y, 0])
            cube([plate_w, plate_h, plate_t]);

        // Top cutout är fortfarande lokal till bottenplattan.
        translate(top_cutout_pos)
            cube(top_cutout_size);

        // OBS:
        // Kabelhålet skärs globalt i model_local(), inte här.
        // Då tar det även displayens ram/beams.
    }
}


// ==========================================================
// SVÄVANDE VÄGG / LÅDA — HULL AV YTTERSTA SMÅ HÅL/PELARE
// ==========================================================

module floating_wall_frame() {
    difference() {
        // Ytterkontur: exakt hull av cylinder(h=5, r=1.5) vid yttersta hålen.
        translate([0, 0, floating_z])
            linear_extrude(height = wall_z_h)
                wall_outer_2d();

        // Innerkontur: offsetad inåt med wall_t.
        // Väggen är solid runt hela.
        translate([0, 0, floating_z - 0.5])
            linear_extrude(height = wall_z_h + 1)
                wall_inner_2d();

        // USB-A kabelöppning för Raspberry Pi 5.
        // Sitter på nedre väggsidan med nuvarande pi_rot=270.
        usb_a_cutout_volume();
    }
}


// ==========================================================
// STÖD TILL SVÄVANDE STRUKTUR
// ==========================================================

module floating_outer_supports_raw() {
    support_h = 5;

    x0 = 0;
    x1 = outer_w;
    y0 = 0;
    y1 = outer_h;

    xi = 12;
    yi = 10;

    // Fyra diagonala hörnstöd, exakt från de yttersta hålcentrum.
    myline(
        [x0, y0, floating_z+1], support_h, support_small_d,
        [x0 + xi, y0 + yi, 0], support_h, support_big_d
    );

    myline(
        [x1, y0, floating_z+1], support_h, support_small_d,
        [x1 - xi, y0 + yi, 0], support_h, support_big_d
    );

    myline(
        [x1, y1, floating_z+1], support_h, support_small_d,
        [x1 - xi, y1 - yi, 0], support_h, support_big_d
    );

    myline(
        [x0, y1, floating_z+1], support_h, support_small_d,
        [x0 + xi, y1 - yi, 0], support_h, support_big_d
    );

    // Extra stöd på övre långsidan.
    // Nedre långsidan hanteras mer medvetet runt USB-cutouten.
    beam(
        [outer_w*0.30, y1, floating_z],
        [outer_w*0.30, y1 - 10, 0],
        h=support_h,
        d=5
    );

    beam(
        [outer_w*0.70, y1, floating_z],
        [outer_w*0.70, y1 - 10, 0],
        h=support_h,
        d=5
    );

    // Nedre långsida: stöd höger om USB-cutouten, under display-sidan.
    beam(
        [outer_w*0.70, y0, floating_z],
        [outer_w*0.70, y0 + 10, 0],
        h=support_h,
        d=5
    );
}

module floating_outer_supports() {
    keep_inside_outer_hull()
        floating_outer_supports_raw();
}


// ==========================================================
// USB-CUTOUT SIDOSTÖD
// ==========================================================

module usb_a_cutout_side_beams_raw() {
    // Två förstärknings-beams på varsin sida om USB-A-öppningen.
    // De går från nedre ytterkanten inåt i lådan.

    x_left  = usb_a_cutout_x - usb_side_beam_gap;
    x_right = usb_a_cutout_x + usb_a_cutout_w + usb_side_beam_gap;

    // Vänster sida om cutouten
    beam(
        [x_left, 0, floating_z],
        [x_left, usb_side_beam_len, 0],
        h = usb_side_beam_h,
        d = usb_side_beam_d
    );

    // Höger sida om cutouten
    beam(
        [x_right, 0, floating_z],
        [x_right, usb_side_beam_len, 0],
        h = usb_side_beam_h,
        d = usb_side_beam_d
    );
}

module usb_a_cutout_side_beams() {
    keep_inside_outer_hull()
        usb_a_cutout_side_beams_raw();
}


// ==========================================================
// LOCKFÄSTEN
// ==========================================================

module lid_mounts() {
    z = floating_z;

    // Lockfästen lite in från hörnen, stora nog för M3-pilothål.
    pts = [
        [lid_tower_inset, lid_tower_inset],
        [outer_w - lid_tower_inset, lid_tower_inset],
        [outer_w - lid_tower_inset, outer_h - lid_tower_inset],
        [lid_tower_inset, outer_h - lid_tower_inset]
    ];

    for (p = pts) {
        translate([p[0], p[1], z])
            lid_tower();
    }
}


// ==========================================================
// HÖRNANKARE / HÅL
// ==========================================================

module corner_anchor_struts_raw() {
    translate([0, 0, 4]) {
        myline([0, 0, 1], 5, 3, [12, 10, -4], 5, 7);
        cylinder(h=3, r=corner_pillar_r);
    }

    translate([outer_w, 0, 4]) {
        myline([0, 0, 1], 5, 3, [-12, 10, -4], 5, 7);
        cylinder(h=5, r=corner_pillar_r);
    }

    translate([outer_w, outer_h, 4]) {
        myline([0, 0, 1], 5, 3, [-12, -10, -4], 5, 7);
        cylinder(h=5, r=corner_pillar_r);
    }

    translate([0, outer_h, 4]) {
        myline([0, 0, 1], 5, 3, [12, -10, -4], 5, 7);
        cylinder(h=5, r=corner_pillar_r);
    }
}

module corner_anchor_struts() {
    keep_inside_outer_hull()
        corner_anchor_struts_raw();
}

module corner_anchor_holes() {
    for (p = corner_centers()) {
        translate([p[0], p[1], 4])
            cylinder(h=20, r=corner_hole_r);
    }
}


// ==========================================================
// RASPBERRY PI 5 — PORTRÄTT, PELARE DIREKT PÅ BOTTEN
// ==========================================================

module raspberry_pi_mount() {
    translate([pi_pos[0], pi_pos[1], 0])
    rotate([0, 0, pi_rot]) {
        for (p = pi_holes) {
            translate([p[0], p[1], pi_standoff_base_z])
                simple_standoff(
                    h = pi_standoff_h,
                    d = pi_standoff_d,
                    screw_d = pi_screw_pilot_d
                );
        }

        if (show_pi_footprint) {
            %translate([0, 0, pi_standoff_base_z + pi_standoff_h])
                cube([pi_size[0], pi_size[1], 1.2]);

            for (p = pi_holes) {
                %translate([p[0], p[1], pi_standoff_base_z + pi_standoff_h])
                    cylinder(h=3, d=3);
            }
        }
    }
}


// ==========================================================
// JC3248S035 DISPLAY — PÅ ANDRA SIDAN FRÅN RPI, ROTERAD 90°
// ==========================================================

module display_mount() {
    translate([display_pos[0], display_pos[1], 0])
    rotate([0, 0, display_rot]) {
        for (p = display_holes) {
            translate([p[0], p[1], display_standoff_base_z])
                simple_standoff(
                    h=display_standoff_h,
                    d=display_standoff_d,
                    screw_d=display_screw_pilot_d
                );
        }

        // Förstärkning mellan display-pelarna.
        // Den ligger direkt på bottenplattan.
        beam(
            [display_holes[0][0], display_holes[0][1], display_standoff_base_z],
            [display_holes[1][0], display_holes[1][1], display_standoff_base_z],
            h=2,
            d=5
        );

        beam(
            [display_holes[1][0], display_holes[1][1], display_standoff_base_z],
            [display_holes[2][0], display_holes[2][1], display_standoff_base_z],
            h=2,
            d=5
        );

        beam(
            [display_holes[2][0], display_holes[2][1], display_standoff_base_z],
            [display_holes[3][0], display_holes[3][1], display_standoff_base_z],
            h=2,
            d=5
        );

        beam(
            [display_holes[3][0], display_holes[3][1], display_standoff_base_z],
            [display_holes[0][0], display_holes[0][1], display_standoff_base_z],
            h=2,
            d=5
        );

        if (show_display_footprint) {
            %translate([0, 0, display_standoff_base_z + display_standoff_h])
                cube([display_size[0], display_size[1], 1.2]);

            for (p = display_holes) {
                %translate([p[0], p[1], display_standoff_base_z + display_standoff_h])
                    cylinder(h=3, d=3);
            }
        }
    }
}


// ==========================================================
// KAMERAFÄSTE — PELARE DIREKT PÅ BOTTEN, HÖGT I Z
// ==========================================================

module camera_mount() {
    translate([camera_pos[0], camera_pos[1], 0]) {
        for (p = camera_holes) {
            translate([p[0], p[1], camera_standoff_base_z])
                simple_standoff(
                    h=camera_standoff_h,
                    d=camera_standoff_d,
                    screw_d=camera_screw_pilot_d
                );
        }

        // Lätt förstärkning mellan kamerans pelare.
        // Den ligger direkt på bottenplattan.
        beam(
            [camera_holes[0][0], camera_holes[0][1], camera_standoff_base_z],
            [camera_holes[1][0], camera_holes[1][1], camera_standoff_base_z],
            h=2,
            d=4.5
        );

        beam(
            [camera_holes[2][0], camera_holes[2][1], camera_standoff_base_z],
            [camera_holes[3][0], camera_holes[3][1], camera_standoff_base_z],
            h=2,
            d=4.5
        );

        beam(
            [camera_holes[0][0], camera_holes[0][1], camera_standoff_base_z],
            [camera_holes[2][0], camera_holes[2][1], camera_standoff_base_z],
            h=2,
            d=4.5
        );

        beam(
            [camera_holes[1][0], camera_holes[1][1], camera_standoff_base_z],
            [camera_holes[3][0], camera_holes[3][1], camera_standoff_base_z],
            h=2,
            d=4.5
        );

        if (show_camera_footprint) {
            %translate([0, 0, camera_standoff_base_z + camera_standoff_h])
                cube([camera_size[0], camera_size[1], 1.0]);
        }
    }
}


// ==========================================================
// DEBUG
// ==========================================================

module debug_keepouts() {
    if (show_keepouts) {
        %cable_cutout_volume();

        %translate(top_cutout_pos)
            cube([top_cutout_size[0], top_cutout_size[1], 2]);
    }

    if (show_wall_footprint) {
        %translate([0, 0, floating_z])
            linear_extrude(height = 1)
                wall_outer_2d();
    }
}


// ==========================================================
// SLUTMODELL
// ==========================================================

module model_local() {
    difference() {
        union() {
            base_plate();

            // Svävande vägg/låda: exakt hull av yttersta små hål/pelare.
            // Solid hela vägen runt, med USB-A-cutout.
            floating_wall_frame();

            // Stöd till svävande struktur, klippta mot ytterhullen.
            floating_outer_supports();

            // Nya stöd på varsin sida om USB-A-cutouten.
            usb_a_cutout_side_beams();

            // Locktorn
            lid_mounts();

            // Hörnankare från ursprungsmodellen, också klippta mot ytterhullen.
            corner_anchor_struts();

            // Direktmonterade komponentpelare
            raspberry_pi_mount();
            display_mount();
            camera_mount();

            debug_keepouts();
        }

        // Hörnhål
        corner_anchor_holes();

        // USB-A-cutout måste skära även beams/stöd,
        // annars blir en beam kvar i öppningen.
        usb_a_cutout_volume();

        // Kabelhålet ska skära genom all geometri,
        // inklusive displayens ram/beams.
        cable_cutout_volume();

        // OBS:
        // Top cutout skär bara base_plate(), inte väggen.
    }
}

translate(origin)
    model_local();