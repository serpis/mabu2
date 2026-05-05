$fn=100;

module line(width, a, b, peg=true) {
    height = 3;
    dir = b - a;
    dist = sqrt(dir.x*dir.x + dir.y*dir.y);

    translate(a) {
        //cylinder(d=width, h=height);
        //if (peg) {
        //    cylinder(d=width, h=height+2);
        //    cylinder(d=8.2, h=height+10);
        //}
        rotate([0, 0, atan2(dir.y, dir.x)]) translate([0, -width/2, 0]) cube([dist, width, height]);
    }
    translate(b) {
        //cylinder(d=width, h=height);
        //if (peg) {
        //    cylinder(d=width, h=height+2);
        //    cylinder(d=8.2, h=height+10);
        //}
    }
}

module myline(from, from_h, from_d, to, to_h, to_d) {
    hull() {
        translate(from)
            cylinder(h = from_h, d = from_d, $fn = 32);

        translate(to)
            cylinder(h = to_h, d = to_d, $fn = 32);
    }
}

difference() {
    translate([5, 5, 0]) {
        translate([0, 0, 4]) {
            myline([0, 0, 1], 5, 3, [12, 10, -4], 5, 7);
            cylinder(h=3, r=1.5);
            //line(3, [0, 0], [12, 10]);
            //translate([12, 10, -4])
            //    cylinder(h=7, r=1.5);
        }

        translate([171, 0, 4]) {
            myline([0, 0, 1], 5, 3, [-12, 10, -4], 5, 7);
            cylinder(h=5, r=1.5);
            //line(3, [0, 0], [-12, 10]);
            //translate([-12, 10, -4])
            //    cylinder(h=7, r=1.5);

        }
        translate([171, 110, 4]) {
            myline([0, 0, 1], 5, 3, [-12, -10, -4], 5, 7);
            cylinder(h=5, d=3);
        }
        translate([0, 110, 4]) {
            myline([0, 0, 1], 5, 3, [12, -10, -4], 5, 7);
            cylinder(h=5, r=1.5);

            //line(3, [0, 0], [12, -10]);
            //translate([12, -10, -4])
            //    cylinder(h=7, r=1.5);
        }
    }
    translate([5, 5, 0]) {
        translate([0, 0, 4])
            difference() {
                cylinder(h=10, r=0.5);
            }
        translate([171, 0, 4])
            difference() {
                cylinder(h=10, r=0.5);
            }
        translate([171, 110, 4])
            difference() {
                cylinder(h=10, r=0.5);
            }
        translate([0, 110, 4])
            difference() {
                cylinder(h=10, r=0.5);
            }
    }
}

difference() {
    translate([5+(171-152)/2, 5+(110-94)/2, 0]) cube([152, 94, 1]);
    translate([33-1, 119-7-38, 0]) cube([30, 38, 10]); // antenna
    translate([(5+171+5)/2-42/2, 119-64-10+1, 0]) cube([42, 10, 10]); // cable hole
    translate([(5+171+5)/2-12/2, 119-12-4, 0]) cube([12, 12, 10]); // top cutout
}

//translate([0, 0, 4])
//    line(3, [5,5],[17, 15]);
//translate([17, 15, 0])
//    cylinder(h=7, r=1.5);
