package kr.dogfoot.hwp2hwpx.cli;

import kr.dogfoot.hwp2hwpx.Hwp2Hwpx;
import kr.dogfoot.hwplib.object.HWPFile;
import kr.dogfoot.hwplib.reader.HWPReader;
import kr.dogfoot.hwpxlib.object.HWPXFile;
import kr.dogfoot.hwpxlib.writer.HWPXWriter;

public class Hwp2HwpxCLI {
    public static void main(String[] args) {
        if (args.length != 2) {
            System.err.println("Usage: java -jar hwp2hwpx.jar <input.hwp> <output.hwpx>");
            System.exit(1);
        }
        
        String inputPath = args[0];
        String outputPath = args[1];
        
        try {
            HWPFile hwpFile = HWPReader.fromFile(inputPath);
            HWPXFile hwpxFile = Hwp2Hwpx.toHWPX(hwpFile);
            HWPXWriter.toFilepath(hwpxFile, outputPath);
            System.out.println("변환 완료: " + inputPath + " -> " + outputPath);
        } catch (Exception e) {
            System.err.println("변환 실패: " + e.getMessage());
            e.printStackTrace();
            System.exit(1);
        }
    }
} 